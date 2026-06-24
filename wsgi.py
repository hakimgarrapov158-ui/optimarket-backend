# Этот файл нужен для PythonAnywhere
# Путь: /var/www/твой_логин_pythonanywhere_com_wsgi.py

import sys
import os

# Укажи путь к своей папке проекта
project_home = '/home/ТВОЙ_ЛОГИН/optimarket-backend'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from main import app as application
