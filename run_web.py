"""Launch the CiteWise web app.

    python run_web.py

then open http://127.0.0.1:8000 in your browser.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("webapp.server:app", host="127.0.0.1", port=8000, reload=False)
