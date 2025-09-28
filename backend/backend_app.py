"""
Flask + Ariadne GraphQL backend for IMDB CRUD.

Endpoints:
 - GET  /graphql   : GraphQL Explorer (Apollo Sandbox UI)
 - POST /graphql   : GraphQL endpoint (accepts queries & mutations)
 - GET  /health    : Liveness

Data store: backend/imdb.json

Run:
    cd backend
    pip install -r requirements.txt
    python app.py
"""
import json
import threading
import os
import requests
from flask import Flask, request, jsonify
from ariadne import graphql_sync, make_executable_schema, ObjectType, snake_case_fallback_resolvers
from ariadne.explorer import ExplorerApollo

DATA_FILE = os.path.join(os.path.dirname(__file__), "imdb.json")
LOCK = threading.RLock()

type_defs = """
type Movie {
  id: ID!
  title: String!
  genre: [String!]!
  description: String
  directors: [String!]!
  actors: [String!]!
  year: Int
  runtime: Int
  rating: Float
  votes: Int
  revenue: Float
}

input MovieInput {
  title: String!
  genre: [String!]
  description: String
  directors: [String!]
  actors: [String!]
  year: Int
  runtime: Int
  rating: Float
  votes: Int
  revenue: Float
}

input UpdateMovieInput {
  title: String
  genre: [String!]
  description: String
  directors: [String!]
  actors: [String!]
  year: Int
  runtime: Int
  rating: Float
  votes: Int
  revenue: Float
}

type Query {
  movies(title: String, year: Int, minRating: Float): [Movie!]!
  movieById(id: ID!): Movie
}

type Mutation {
  createMovie(input: MovieInput!): Movie!
  updateMovie(id: ID!, input: UpdateMovieInput!): Movie
  deleteMovie(id: ID!): Boolean!
}
"""

# ---------- helpers ----------
def safe_load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []

def read_data():
    raw = safe_load_json(DATA_FILE)
    
    # Normalize any legacy-shaped records to the canonical format
    normalized = []
    for r in raw:
        try:
            normalized.append(_to_canonical(r))
        except Exception:
            # skip records we cannot normalize
            continue
   
    return normalized

def write_data(data):
    
    with LOCK:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def call_ollama(prompt, model="ollama/gpt-4o-mini"):
    """Call an Ollama-like API to generate a GraphQL query or textual suggestion.
    Expects environment variables:
      OLLAMA_API_URL (e.g. http://localhost:11434 or https://api.ollama.com)
      OLLAMA_API_KEY (optional)
    Returns string or raises on network error.
    """
    base = os.environ.get("OLLAMA_API_URL")
    if not base:
        raise RuntimeError("OLLAMA_API_URL is not set")
    url = base.rstrip('/') + '/api/generate'
    headers = {'Content-Type': 'application/json'}
    api_key = os.environ.get('OLLAMA_API_KEY')
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    body = {
        "model": model,
        "prompt": f"Generate a GraphQL create mutation for storing a movie with these fields: {json.dumps(prompt)}\nReturn only the GraphQL mutation as plain text.",
        "max_tokens": 512
    }

    resp = requests.post(url, json=body, headers=headers, timeout=10)
    resp.raise_for_status()

    # Try to decode JSON, but fall back to raw text
    try:
        data = resp.json()
    except Exception:
        txt = resp.text.strip()
        print('Ollama returned non-JSON response:', txt[:1000])
        return txt

    # Helper: recursively extract text from various known shapes
    def extract_text(obj):
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj
        if isinstance(obj, list):
            parts = [extract_text(x) for x in obj]
            parts = [p for p in parts if p]
            return '\n'.join(parts) if parts else None
        if isinstance(obj, dict):
            # common keys
            for k in ('text', 'output', 'result', 'response'):
                if k in obj and obj[k]:
                    return extract_text(obj[k])

            # choices/results arrays
            for arr_key in ('choices', 'results'):
                if arr_key in obj and isinstance(obj[arr_key], list):
                    out = extract_text(obj[arr_key])
                    if out:
                        return out

            # some APIs return nested message->content lists
            if 'message' in obj:
                return extract_text(obj['message'])
            if 'content' in obj:
                return extract_text(obj['content'])

            # scan values for any textual fields
            for v in obj.values():
                t = extract_text(v)
                if t:
                    return t

        return None

    text = extract_text(data)
    if text:
        return text

    # Special handling for Ollama-like choices/content structures
    # e.g. { choices: [{ content: [{ type: 'output_text', text: '...' }] }] }
    try:
        if isinstance(data, dict) and 'choices' in data:
            for ch in data['choices']:
                if isinstance(ch, dict):
                    # content array
                    cont = ch.get('content') or ch.get('message') or ch.get('text')
                    txt = extract_text(cont)
                    if txt:
                        return txt
    except Exception:
        pass

    # If nothing found, log raw JSON for debugging and return None
    try:
        print('Ollama response (unparsed):', json.dumps(data)[:2000])
    except Exception:
        print('Ollama response (unparsed, raw):', str(data)[:2000])
    return None


def _split_list_field(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return []

def _to_canonical(raw):
    """
    Convert a raw record (could have keys like Ids, Title, Genre, etc.)
    into canonical format used by the app:
    {
      "id": "1",
      "title": "...",
      "genre": [...],
      "description": "...",
      "directors": [...],
      "actors": [...],
      "year": 2014,
      "runtime": 121,
      "rating": 8.1,
      "votes": 123,
      "revenue": 12.3
    }
    """
    # Fetch candidates
    id_val = raw.get("Ids") or raw.get("id") or raw.get("ID") or raw.get("Id")
    title = raw.get("Title") or raw.get("title") or raw.get("NAME") or ""
    genre = raw.get("Genre") or raw.get("genre") or raw.get("genres") or []
    description = raw.get("Description") or raw.get("description") or raw.get("desc") or None
    directors = raw.get("Director") or raw.get("directors") or raw.get("Director(s)") or []
    actors = raw.get("Actors") or raw.get("actors") or raw.get("Cast") or []
    year = raw.get("Year") or raw.get("year")
    runtime = raw.get("Runtime") or raw.get("runtime")
    rating = raw.get("Rating") or raw.get("rating")
    votes = raw.get("Votes") or raw.get("votes")
    revenue = raw.get("Revenue") or raw.get("revenue")

    # Normalize types
    try:
        id_str = str(id_val) if id_val is not None else None
    except Exception:
        id_str = None

    try:
        year = int(year) if year not in (None, "") else None
    except Exception:
        year = None
    try:
        runtime = int(runtime) if runtime not in (None, "") else None
    except Exception:
        runtime = None
    try:
        rating = float(rating) if rating not in (None, "") else None
    except Exception:
        rating = None
    try:
        votes = int(votes) if votes not in (None, "") else None
    except Exception:
        votes = None
    try:
        revenue = float(revenue) if revenue not in (None, "") else None
    except Exception:
        revenue = None

    canonical = {
        "id": id_str,
        "title": title or "",
        "genre": _split_list_field(genre),
        "description": description,
        "directors": _split_list_field(directors),
        "actors": _split_list_field(actors),
        "year": year,
        "runtime": runtime,
        "rating": rating,
        "votes": votes,
        "revenue": revenue,
    }
    return canonical

# ---------- resolvers ----------
query = ObjectType("Query")
mutation = ObjectType("Mutation")

@query.field("movies")
def resolve_movies(*_, title=None, year=None, minRating=None):
    data = read_data()
    results = []
    for m in data:
        # Skip invalid
        if not m.get("id") and not m.get("title"):
            continue
        ok = True
        if title:
            if title.lower() not in (m.get("title") or "").lower():
                ok = False
        if year and m.get("year") != year:
            ok = False
        if minRating is not None:
            r = m.get("rating")
            if r is None or r < minRating:
                ok = False
        if ok:
            results.append(m)
    return results

@query.field("movieById")
def resolve_movie_by_id(*_, id):
    data = read_data()
    for m in data:
        if str(m.get("id")) == str(id):
            return m
    return None

@mutation.field("createMovie")
def resolve_create_movie(*_, input):
    """
    input: MovieInput
    We'll store data in canonical format. If input misses lists, ensure lists exist.
    """
    #print("Creating movie with input:", input)
    # Attempt to ask Ollama to generate a GraphQL mutation for this input. This is best-effort.
    generated_graphql = None
    # Allow forcing a fake Ollama response for testing via env var
    if os.environ.get('FORCE_FAKE_OLLAMA') == '1':
        generated_graphql = '''mutation CreateMovie($input: MovieInput!) {
  createMovie(input: $input) {
    id
    title
    year
    rating
  }
}'''
    else:
        try:
            generated_graphql = call_ollama(input)
        except Exception as e:
            # non-fatal; log and continue
            print('Ollama generation failed:', str(e))

    with LOCK:
        data = read_data()
        # compute new numeric id
        max_id = 0
        for rec in data:
            try:
                mid = int(rec.get("id") or 0)
                if mid > max_id:
                    max_id = mid
            except Exception:
                continue
        new_id = max_id + 1
        # build record
        record = {
            "id": str(new_id),
            "title": input.get("title") or "",
            "genre": input.get("genre") or [],
            "description": input.get("description"),
            "directors": input.get("directors") or [],
            "actors": input.get("actors") or [],
            "year": input.get("year"),
            "runtime": input.get("runtime"),
            "rating": input.get("rating"),
            "votes": input.get("votes"),
            "revenue": input.get("revenue"),
        }
        # normalize types for lists
        record["genre"] = _split_list_field(record["genre"])
        record["directors"] = _split_list_field(record["directors"])
        record["actors"] = _split_list_field(record["actors"])
        data.append(record)
        #last_val = data[-1]
        #print()
        #print("Appended record:", last_val)
        #orig =  data[0]
        #print("original value", orig)
        write_data(data)
        # attach generated_graphql to the returned record for client visibility (non-standard field)
        if generated_graphql:
            record['generated_graphql'] = generated_graphql
        return record

@mutation.field("updateMovie")
def resolve_update_movie(*_, id, input):
    with LOCK:
        data = read_data()
        for idx, rec in enumerate(data):
            if str(rec.get("id")) == str(id):
                # update keys - accept both scalar and lists; overwrite fields provided
                for k, v in input.items():
                    if k in ("genre", "directors", "actors"):
                        rec[k] = _split_list_field(v)
                    else:
                        rec[k] = v
                data[idx] = rec
                write_data(data)
                return rec
    return None

@mutation.field("deleteMovie")
def resolve_delete_movie(*_, id):
    with LOCK:
        data = read_data()
        new_data = [m for m in data if str(m.get("id")) != str(id)]
        deleted = len(new_data) != len(data)
        if deleted:
            write_data(new_data)
        return deleted

# ---------- app ----------
schema = make_executable_schema(type_defs, [query, mutation, snake_case_fallback_resolvers])
app = Flask(__name__)
explorer_html = ExplorerApollo().html(None)

@app.route("/graphql", methods=["GET"])
def graphql_explorer():
    return explorer_html, 200

@app.route("/graphql", methods=["POST"])
def graphql_server():
    payload = request.get_json()
    success, result = graphql_sync(schema, payload, context_value=request, debug=app.debug)
    return jsonify(result), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # initialize file with one canonical sample if empty
    if not os.path.exists(DATA_FILE) or os.path.getsize(DATA_FILE) == 0:
        sample = [
            {
                "id": "1",
                "title": "Guardians of the Galaxy",
                "genre": ["Action", "Adventure", "Sci-Fi"],
                "description": "A group of intergalactic criminals are forced to work together to stop a fanatical warrior from taking control of the universe.",
                "directors": ["James Gunn"],
                "actors": ["Chris Pratt", "Vin Diesel", "Bradley Cooper", "Zoe Saldana"],
                "year": 2014,
                "runtime": 121,
                "rating": 8.1,
                "votes": 757074,
                "revenue": 333.13
            }
        ]
        write_data(sample)

    app.run(host="0.0.0.0", port=4000, debug=True)
