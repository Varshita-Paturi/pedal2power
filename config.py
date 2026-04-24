import os

class Config:
    # Use a random secret key if not provided in env, so restarts invalidate old sessions
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24)
    SQLALCHEMY_DATABASE_URI = 'sqlite:///pedalpower.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEBUG = True
