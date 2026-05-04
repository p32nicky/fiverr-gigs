import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mangum import Mangum
from app.web import app

handler = Mangum(app, lifespan="off")
