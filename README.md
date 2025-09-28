# IMDB Movie Database Chatbot

This project is a web application that allows users to interact with a database of IMDB movies using natural language. It consists of a Python backend using Flask and GraphQL, and a frontend built with Streamlit.

## Project Structure

- `backend/`: Contains the Flask/GraphQL server.
  - `app.py`: The main backend application file.
  - `IMDB-Movie-Data.csv`: The raw movie data.
  - `imdb.json`: The JSON data used by the backend.
  - `requirements.txt`: Python dependencies for the backend.
- `frontend/`: Contains the Streamlit user interface.
  - `streamlit_app.py`: The main frontend application file.

## Getting Started

### Prerequisites

- Python 3.7+
- `pip` for package installation

### Backend Setup

1.  **Navigate to the backend directory:**
    ```bash
    cd backend
    ```

2.  **Install the required Python packages:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the backend server:**
    ```bash
    python app.py
    ```
    The backend server will start on `http://localhost:4000`. You can access the GraphQL playground at `http://localhost:4000/graphql`.

### Frontend Setup

1.  **Navigate to the frontend directory:**
    ```bash
    cd frontend
    ```

2.  **Install Streamlit if you haven't already:**
    ```bash
    pip install streamlit
    ```

3.  **Run the frontend application:**
    ```bash
    streamlit run streamlit_app.py
    ```
    The frontend will be accessible at `http://localhost:8501`.

## How it Works

- The **frontend** is a Streamlit application that provides a chat interface.
- User input in natural language is sent to a local LLM (like Ollama) to be translated into a GraphQL query.
- The GraphQL query is sent to the **backend**.
- The **backend** is a Flask server with a GraphQL endpoint powered by Ariadne. It resolves the query, interacts with the `imdb.json` data file, and returns the result.
- The results are displayed on the frontend as movie cards or status messages.

## Technologies Used

- **Backend**:
  - Flask
  - Ariadne (for GraphQL)
  - Python
- **Frontend**:
  - Streamlit
- **Data**:
  - Pandas (for initial data conversion)
- **Natural Language to GraphQL**:
  - Can be configured to use a local LLM via an API endpoint (e.g., Ollama).
  - Includes a deterministic fallback parser for basic commands.


## Create Movies
Name and data should be given

## Update Movies 
Should provide ID with data (e.g. "update movie id 1020 with year 2025")

## Delete movies
Should provide ID while deleting movies (e.g. "delete id 1020")

