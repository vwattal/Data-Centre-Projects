"""Main landing page — /"""
from flask import Blueprint, render_template
from datetime import datetime

bp = Blueprint('index', __name__)


@bp.route('/')
def index():
    current_date = datetime.now().strftime('%B %d, %Y')
    return render_template('index.html', current_date=current_date)
