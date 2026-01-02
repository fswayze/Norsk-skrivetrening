import sqlite3
import os
from flask import Flask, render_template, request, redirect, url_for, session, Blueprint
from dotenv import load_dotenv
import random
from datetime import datetime
from openai import OpenAI
from pydantic import BaseModel, Field
from typing import Literal, List
from ai.evaluator import evaluate_translation
from db import get_db_connection, init_db, seed_db
from routes import register_routes
from filters import register_filters


app = Flask(__name__)
load_dotenv()
app.secret_key = os.getenv("FLASK_SECRET_KEY")


with app.app_context():
    init_db()
    seed_db()

register_routes(app)
register_filters(app)