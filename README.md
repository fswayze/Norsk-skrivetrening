# Norsk Skrivetrening
![Welcome troll](static/welcome_troll.png)

A web-based game for improving written Norwegian through exam-style translation practice and detailed, structured feedback.

[Video Demo](https://www.loom.com/share/44ad0fa2339a4c5e964685c7e89d628c)

## Overview

Norsk Skrivetrening helps learners practice written Norwegian by translating short English sentences across CEFR levels (A1–C2). Each submission is evaluated in two stages:

1. **LanguageTool** for fast grammar and spelling checks  
2. **LLM-based evaluation** for deeper feedback on correctness, naturalness, and common learner mistakes

The game tracks progress, adapts difficulty, and presents detailed feedback for each tanslation.

## Key Features

- CEFR-aligned levels (A1–C2) with adaptive progression
- LLM-generated, exam-style feedback with concrete correction suggestions
- Per-game history view with all attempts and explanations
- Keyboard-first interaction (Enter to submit, Shift+Enter for newline)
- SQLite-backed persistence for games, attempts, and feedback
- Built-in evaluation harness for regression-testing grading accuracy and latency

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** Jinja templates, vanilla JS, CSS
- **Database:** SQLite
- **Evaluation:** LanguageTool + OpenAI API

## Getting started locally
1. Clone the repository
git clone https://github.com/fswayze/Norsk-skrivetrening.git
cd Norsk-skrivetrening
2. Create a virtual environment
```
python3 -m venv venv
source venv/bin/activate
```
3. Install dependencies
```
pip install -r requirements.txt
```
4. Set environment variables
Create a .env file:
OPENAI_API_KEY=your_api_key_here
5. Run the app
```
flask --app app.py run
```
open http://127.0.0.1:5000

## Why this project

I’ve been learning Norwegian for the past four years, and while my reading and listening comprehension improved steadily, my grammar and writing lagged behind. I built Norsk Skrivetrening to create a more effective way to practice written Norwegian in a focused, low-friction format.

I also wanted to explore how LLMs can provide useful, structured feedback rather than generic correctness scores.

