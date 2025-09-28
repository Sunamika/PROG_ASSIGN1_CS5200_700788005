"""
Streamlit frontend with a chatbot-like UI and movie card display.

- User types natural language commands in a chat box.
- Message is translated into GraphQL (via local LLM or fallback parser).
- GraphQL is sent to backend Flask app at http://localhost:8000/graphql.
- Results are displayed in a chat history with movie cards.
"""

import streamlit as st
import requests
import os
import re

BACKEND_GRAPHQL_URL = os.getenv("BACKEND_GRAPHQL_URL", "http://localhost:4000/graphql")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

st.set_page_config(page_title="🎬 IMDB Chatbot", layout="centered")
st.title("🎬 IMDB Movie Chatbot")

# ---- Session State ----
if "messages" not in st.session_state:
    st.session_state.messages = []


# ---- Helper Functions ----
def call_ollama(prompt: str):
    # Normalize base URL and try a few endpoints commonly used by Ollama-like services
    endpoints = [OLLAMA_API_URL.rstrip('/') + p for p in ('/api/generate', '/api/complete', '')]

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "max_tokens": 512,
        "temperature": 0.0,
    }

    def extract_text(obj):
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            for k in ("text", "generated_text", "output", "result", "choices", "content"):
                if k in obj and obj[k]:
                    return extract_text(obj[k])
            # scan nested values
            for v in obj.values():
                t = extract_text(v)
                if t:
                    return t
        if isinstance(obj, list):
            parts = [extract_text(x) for x in obj]
            parts = [p for p in parts if p]
            return "\n".join(parts) if parts else None
        return None

    last_exc = None
    for url in endpoints:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()

            # Some Ollama builds stream NDJSON responses (one JSON object per line).
            # Example lines include {"response":"Hello","done":false} ... {"response":"","done":true}
            body = resp.text or ''
            # Fast path: if body looks like NDJSON (multiple JSON objects separated by newlines), parse each line
            if '\n' in body and body.strip().startswith('{'):
                out_parts = []
                done = False
                for line in body.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = __import__('json').loads(line)
                    except Exception:
                        # not a JSON line, skip
                        continue
                    # prefer 'response' key used by your Ollama output
                    resp_text = obj.get('response') or obj.get('text') or obj.get('output') or None
                    if isinstance(resp_text, str) and resp_text:
                        out_parts.append(resp_text)
                    # if done flag present and true, stop
                    if obj.get('done') is True:
                        done = True
                        break
                if out_parts:
                    return ''.join(out_parts)
                # if we reach here, fall back to attempting JSON parse below

            # Not NDJSON: try parse as JSON object
            try:
                data = resp.json()
            except Exception:
                txt = body.strip()
                print('Ollama returned non-JSON response (frontend):', txt[:1000])
                return txt

            text = extract_text(data)
            if text:
                return text

            print('Ollama returned JSON but no text found (frontend). Raw:', data)
            return str(data)

        except Exception as e:
            last_exc = e
            # try next endpoint
            continue
    # all attempts failed
    print('Ollama calls failed (frontend). Last error:', last_exc)
    return None


def deterministic_fallback_to_graphql(nl_text: str) -> str:
    t = nl_text.strip().lower()

    # Update by ID
    if "update" in t or "change" in t:
        m = re.search(r'(?:update|change)\s+(?:movie\s+)?(\d+)', nl_text, re.IGNORECASE)
        if m:
            movie_id = m.group(1)
            # Simple example: update rating. A real implementation would need more parsing.
            return f'mutation {{ updateMovie(id: "{movie_id}", input: {{ rating: 9.0 }}) {{ id title rating }} }}'
        return 'mutation { updateMovie(id: "1", input: { rating: 8.5 }) { id title rating } }'

    # Delete by ID
    if "delete" in t or "remove" in t:
        m = re.search(r'(?:delete|remove)\s+(?:movie\s+)?(\d+)', nl_text, re.IGNORECASE)
        if m:
            movie_id = m.group(1)
            return f'mutation {{ deleteMovie(id: "{movie_id}") }}'
        # Fallback for title-based deletion if you want to keep it for some cases
        m_title = re.search(r'(?:delete|remove)\s+(?:movie\s+)?["\']([^"\']+)["\']', nl_text, re.IGNORECASE)
        if m_title:
            title = m_title.group(1).replace('"', '\\"')
            # Note: This now points to a non-existent mutation, but shows intent.
            # To make this work, you'd need a deleteMovieByTitle mutation again.
            # For this request, we prioritize ID-based deletion.
            return f'# The backend now deletes by ID. Example: delete movie 123\nmutation {{ deleteMovie(id: "0") }}' # return invalid query
        return 'mutation { deleteMovie(id: "0") }' # Default to a safe non-deleting mutation

    # Create
    if "create" in t or "add" in t or "insert" in t:
        # Try to extract a title from "create movie 'title'" or "add 'title'"
        m = re.search(r'(?:create|add|insert)\s+(?:movie\s+)?["\']([^"\']+)["\']', nl_text, re.IGNORECASE)
        title = m.group(1) if m else "Untitled Movie"
        # Escape double quotes in title
        title = title.replace('"', '\\"')
        return f"""
        mutation {{
          createMovie(input: {{ title: "{title}", year: 2024, rating: 7.0 }}) {{
            id title year rating
          }}
        }}
        """

    # Show or list movies
    if "show" in t or "list" in t or "movies" in t:
        m = re.search(r'"([^"]+)"', nl_text) or re.search(r'movie\s+([A-Za-z0-9: ]+)', nl_text)
        if m:
            title = m.group(1).strip().replace('"', '\\"')
            return f"""
            query {{
              movies(title: "{title}") {{
                id title genre description directors actors year runtime rating votes revenue
              }}
            }}
            """
        return """
        query {
          movies {
            id title genre description directors actors year runtime rating votes revenue
          }
        }
        """

    # Default safe query
    return """
    query {
      movies {
        id title genre description directors actors year runtime rating votes revenue
      }
    }
    """


def translate_to_graphql(user_text: str) -> str:
    prompt = (
        "Convert this natural language command into a GraphQL query or mutation "
        "for the IMDB schema. The schema has a `Movie` type and a `MovieInput` type. "
        "The `movies` query returns a flat list of `Movie` objects, not a paginated "
        "list with `edges` and `node`. For example: `query { movies { title } }`. "
        "The `createMovie` mutation takes an `input` argument of type `MovieInput` "
        "and MUST include a selection set, for example: "
        "`createMovie(input: {title: \"My Movie\"}) { id title year }`. "
        "The `updateMovie` mutation also takes an `id` and `input` and MUST include a selection set. For example: `mutation { updateMovie(id: \"1\", input: {year: 2022}) { id title year } }`. "
        "The `deleteMovie` mutation takes an `id` and returns a `Boolean!`, so it must not have a selection set. For example: `mutation { deleteMovie(id: \"1\") }`. "
        "Filtering movies should be done with arguments directly on the `movies` field, like `movies(title: \"Inception\")`. Do not use a `where` clause. "
        "Output only GraphQL, with no explanations or code fences.\n\n"
        f'Command: "{user_text}"\n\nGraphQL:'
    )
    raw = call_ollama(prompt)
    print("Ollama raw response:", raw)
    if raw:
        gql = sanitize_graphql(raw)
        # Check if the result is a plausible query/mutation before returning
        if gql and re.search(r"^\s*(?:query|mutation)\b", gql, re.IGNORECASE):
            return gql
    # If Ollama fails or returns invalid GraphQL, use the robust fallback
    return deterministic_fallback_to_graphql(user_text)


def sanitize_graphql(gql: str) -> str:
    """
    Performs light sanitization on raw GraphQL output from an LLM.
    - Removes markdown code fences.
    - Strips leading/trailing whitespace.
    - Unescapes quotes.
    """
    s = gql.strip()
    # Remove triple-backtick fences with optional language tag
    s = re.sub(r"^```[a-zA-Z0-9_-]*\n|\n```$", "", s)
    # Remove single backticks wrapping the whole query
    if s.startswith('`') and s.endswith('`'):
        s = s[1:-1].strip()
    # Unescape common backslash-escaped quotes that LLMs sometimes emit (e.g. \"title\")
    s = re.sub(r'\\(["\'])', r"\1", s)
    
    return s.strip()


def execute_graphql(gql: str):
    try:
        r = requests.post(BACKEND_GRAPHQL_URL, json={"query": gql}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        # Try to extract a more specific error from the response if possible
        try:
            error_json = r.json()
            if "errors" in error_json:
                return error_json
        except Exception:
            pass
        return {"errors": [{"message": str(e)}]}


# ---- Rendering Helpers ----
def render_movie_card(movie: dict):
    """Pretty display for a single movie."""
    with st.container():
        st.markdown("---")
        st.subheader(f'{movie.get("title", "Unknown Title")} (ID: {movie.get("id")})')
        cols = st.columns([2, 1])
        with cols[0]:
            st.write(f"**Year:** {movie.get('year')}")
            st.write(f"**Genre:** {', '.join(movie.get('genre', []))}")
            st.write(f"**Directors:** {', '.join(movie.get('directors', []))}")
            st.write(f"**Actors:** {', '.join(movie.get('actors', []))}")
            st.write(f"**Runtime:** {movie.get('runtime')} min")
            st.write(f"**Rating:** ⭐ {movie.get('rating')} ({movie.get('votes')} votes)")
            st.write(f"**Revenue:** ${movie.get('revenue')}M")
        with cols[1]:
            st.write("🎬 Movie Info")
        st.write(movie.get("description", "_No description available._"))


# ---- Chat Display ----
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant" and isinstance(msg["content"], dict):
            data = msg["content"]
            if "errors" in data:
                st.error("An error occurred with the GraphQL query:")
                for error in data["errors"]:
                    st.error(f"- {error.get('message')}")
            elif "data" in data and data["data"]:
                # movies list response
                if "movies" in data["data"]:
                    movies = data["data"]["movies"]
                    if not movies:
                        st.info("No movies found.")
                    else:
                        for m in movies:
                            render_movie_card(m)
                # createMovie single-object response
                elif "createMovie" in data["data"]:
                    cm = data["data"]["createMovie"]
                    if isinstance(cm, dict):
                        title = cm.get("title") or cm.get("name")
                        cid = cm.get("id")
                        st.success(f"Created movie: {title} (ID: {cid})")
                        render_movie_card(cm)
                    else:
                        st.json(data)
                # updateMovie response
                elif "updateMovie" in data["data"]:
                    um = data["data"]["updateMovie"]
                    if isinstance(um, dict):
                        title = um.get("title") or um.get("name")
                        cid = um.get("id")
                        st.success(f"Updated movie: {title} (ID: {cid})")
                        render_movie_card(um)
                    else:
                        st.json(data)
                # deleteMovie or deleteMovieByTitle response
                elif "deleteMovie" in data["data"] or "deleteMovieByTitle" in data["data"]:
                    # backend may return a simple boolean or message
                    val = data["data"].get("deleteMovie") or data["data"].get("deleteMovieByTitle")
                    if isinstance(val, bool):
                        st.success("Movie deleted successfully." if val else "Could not delete movie (it may not exist).")
                    elif isinstance(val, str):
                        st.success(val)
                    else:
                        st.json(data)
                else:
                    st.json(data)
            elif "data" in data and data["data"] is None:
                st.info("The query executed successfully, but returned no data. This can happen with mutations.")
            else:
                st.json(data)
        else:
            st.write(msg["content"])


# ---- Chat Input ----
if user_input := st.chat_input("Type your movie command..."):
    # User
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # Translate
    gql = translate_to_graphql(user_input)

    # Execute
    result = execute_graphql(gql)
    st.session_state.messages.append({"role": "assistant", "content": result})
    with st.chat_message("assistant"):
        if "errors" in result:
            st.error("An error occurred with the GraphQL query:")
            for error in result["errors"]:
                st.error(f"- {error.get('message')}")
        elif "data" in result and result["data"]:
            if "movies" in result["data"]:
                movies = result["data"]["movies"]
                if not movies:
                    st.info("No movies found.")
                else:
                    for m in movies:
                        render_movie_card(m)
            elif "createMovie" in result["data"]:
                cm = result["data"]["createMovie"]
                if isinstance(cm, dict):
                    title = cm.get("title") or cm.get("name")
                    cid = cm.get("id")
                    st.success(f"Created movie: {title} (ID: {cid})")
                    render_movie_card(cm)
                else:
                    st.json(result)
            elif "updateMovie" in result["data"]:
                um = result["data"]["updateMovie"]
                if isinstance(um, dict):
                    title = um.get("title") or um.get("name")
                    cid = um.get("id")
                    st.success(f"Updated movie: {title} (ID: {cid})")
                    render_movie_card(um)
                else:
                    st.json(result)
            elif "deleteMovie" in result["data"] or "deleteMovieByTitle" in result["data"]:
                val = result["data"].get("deleteMovie") or result["data"].get("deleteMovieByTitle")
                if isinstance(val, bool):
                    st.success("Movie deleted successfully." if val else "Could not delete movie (it may not exist).")
                elif isinstance(val, str):
                    st.success(val)
                else:
                    st.json(result)
            else:
                st.json(result)
        elif "data" in result and result["data"] is None:
            st.info("The query executed successfully, but returned no data. This can happen with mutations like delete.")
        else:
            st.json(result)
