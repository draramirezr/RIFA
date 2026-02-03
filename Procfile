web: python manage.py migrate && python manage.py collectstatic --noinput && gunicorn rifa_site.wsgi:application --bind 0.0.0.0:$PORT

