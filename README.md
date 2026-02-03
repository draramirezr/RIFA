# Sistema de Rifas (Django)

Web moderna para publicar rifas y permitir que el cliente **compre boletos** subiendo la **foto del comprobante de transferencia**. Incluye panel `/admin/` para gestionar rifas y aprobar compras.

## Requisitos

- Python (Windows): ya tienes `py` instalado.

## Instalar dependencias

```powershell
py -m pip install --user Django Pillow whitenoise PyMySQL
```

## Ejecutar en local

```powershell
cd "z:\Proyectos Soluciones\Rifa"
py manage.py migrate
py manage.py createsuperuser
py manage.py runserver
```

Luego abre:

- Web: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`

## Flujo

- **Inicio**: lista rifas activas.
- **Detalle de rifa**: muestra información y botón “Comprar”.
- **Comprar**: formulario + upload de imagen (comprobante). La compra queda **pendiente**.
- **Admin**: aprobar/rechazar compras desde `TicketPurchase`.

## MySQL (recomendado en producción)

1) Crea una base de datos en MySQL, por ejemplo `rifa_db`.

2) En PowerShell define estas variables de entorno (ejemplo):

```powershell
$env:DB_ENGINE="mysql"
$env:DB_NAME="rifa_db"
$env:DB_USER="root"
$env:DB_PASSWORD="TU_PASSWORD"
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
```

3) Ejecuta migraciones:

```powershell
py manage.py migrate
```

Notas:
- En `rifa_site/settings.py` la configuración se toma de variables de entorno para no guardar credenciales en el código.
- Por compatibilidad en Windows usamos `PyMySQL` (sin compilación).

## Emails (notificación de compras con comprobante)

Por defecto en desarrollo, Django imprime los correos en la terminal.

Para habilitar el envío real (SMTP) y que llegue el comprobante al correo del administrador:

```powershell
$env:SEND_PURCHASE_EMAILS="1"
$env:PURCHASE_NOTIFY_EMAIL="tu-correo@dominio.com"

# SMTP (ejemplo)
$env:EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
$env:EMAIL_HOST="smtp.tudominio.com"
$env:EMAIL_PORT="587"
$env:EMAIL_HOST_USER="usuario"
$env:EMAIL_HOST_PASSWORD="password"
$env:EMAIL_USE_TLS="1"
$env:DEFAULT_FROM_EMAIL="Rifas <no-reply@tudominio.com>"
```

## Tailwind (sin CDN, listo para producción)

Como aquí no usamos Node/npm, se usa el **Tailwind Standalone CLI** (Windows) en `tools\tailwindcss.exe`.

Compilar CSS (producción):

```powershell
cd "z:\Proyectos Soluciones\Rifa"
.\tools\tailwindcss.exe -i .\static\src\tailwind.css -o .\static\dist\tailwind.css --minify
```

Modo desarrollo (watch):

```powershell
.\tools\tailwindcss.exe -i .\static\src\tailwind.css -o .\static\dist\tailwind.css --watch
```

## Deploy en Railway (Django)

### Variables de entorno recomendadas

```text
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=pon-una-clave-segura-larga
DJANGO_ALLOWED_HOSTS=.railway.app
DJANGO_CSRF_TRUSTED_ORIGINS=https://TU-DOMINIO.railway.app

DB_ENGINE=mysql
DB_HOST=ballast.proxy.rlwy.net
DB_PORT=10669
DB_USER=root
DB_PASSWORD=TU_PASSWORD
DB_NAME=rifas
DB_SSL=1

# Para ver imágenes públicas (rifas/logos) en producción
SERVE_PUBLIC_MEDIA=1
```

### Comandos
- **Start**: Railway usa `Procfile` (ya incluido).
- **Migraciones**: `python manage.py migrate`
- **Static** (si lo requieres): `python manage.py collectstatic --noinput`

### Media (uploads)
Railway tiene filesystem efímero. Para que las imágenes subidas no se pierdan, monta un **Volume** en:
- `MEDIA_ROOT` → `/app/media`

