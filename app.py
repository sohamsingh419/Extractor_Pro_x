import os
from flask import Flask

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 8000))

@app.route("/")
def home():
    return "Bot is alive!"

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
