NorskSkrivetrening 🇳🇴
NorskSkrivetrening is a focused writing practice tool for learners who want to become more confident in written Norwegian.

🚀 Getting started locally
1. Clone the repository
git clone https://github.com/fswayze/Norsk-skrivetrening.git
cd Norsk-skrivetrening
2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate
3. Install dependencies
pip install -r requirements.txt
(If requirements.txt does not exist yet, you can add it later.)
4. Set environment variables
Create a .env file:
OPENAI_API_KEY=your_api_key_here
⚠️ .env should never be committed to GitHub.
5. Run the app
python app.py
Then open:
http://127.0.0.1:5000
