"""Run the ManufacturerAI web server: python -m src"""

import uvicorn

if __name__ == "__main__":
    print("Starting ManufacturerAI server on http://localhost:8000")
    uvicorn.run("src.web.server:app", host="127.0.0.1", port=8000, reload=True)
