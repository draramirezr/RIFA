web: python manage.py migrate && python manage.py collectstatic --noinput && gunicorn rifa_site.wsgi:application --bind 0.0.0.0:$PORT --access-logfile - --error-logfile - --capture-output --log-level info --timeout ${GUNICORN_TIMEOUT:-180} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30}

