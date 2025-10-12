import azure.functions as func
import requests
import logging
from msal import PublicClientApplication

CLIENT_ID = '2a0744a0-e102-4af1-9802-362cc4219455'
AUTHORITY = 'https://login.microsoftonline.com/consumers'
SCOPES = ['https://graph.microsoft.com/Mail.Read']
GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'

TOKENS = {}
INBOX_NAMES = ['Bandeja de entrada', 'Inbox']

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="auth")
def auth(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Auth route triggered.')

    try:
        msal_app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

        # Iniciar flujo de autenticación del dispositivo
        device_flow = msal_app.initiate_device_flow(scopes=SCOPES)
        if 'user_code' not in device_flow:
            return func.HttpResponse("Error al iniciar el flujo de dispositivo.", status_code=500)

        # Instrucciones para el usuario
        html = f"""
        <html>
        <body>
            <h1>Microsoft Graph Authorization</h1>
            <p>1️⃣ Ve a este enlace para autorizar:</p>
            <a href="{device_flow['verification_uri']}">{device_flow['verification_uri']}</a>
            <p>2️⃣ Escribe el código: <b>{device_flow['user_code']}</b></p>
            <p>3️⃣ Luego de autorizar, <a href="/api/get_folders">haz clic aquí</a> para obtener tus carpetas.</p>
        </body>
        </html>
        """
    
        TOKENS['device_flow'] = device_flow
        #result = msal_app.acquire_token_by_device_flow(device_flow)

        #if 'access_token' in result:
        #    TOKENS['access_token'] = result['access_token']
        #    TOKENS['headers'] = {"Authorization": f"Bearer {TOKENS['access_token']}"}

        return func.HttpResponse(html, mimetype="text/html", status_code=200)

    except Exception as e:
        logging.error("Error durante autenticación: %s", str(e))
        return func.HttpResponse(f"Error durante autenticación: {str(e)}", status_code=500)

@app.route(route="get_folders")
def get_folders(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Intentando obtener carpetas de correo...")
    try:
        msal_app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
        result = msal_app.acquire_token_by_device_flow(TOKENS['device_flow'])

        TOKENS['access_token'] = result['access_token']
        TOKENS['headers'] = {"Authorization": f"Bearer {TOKENS['access_token']}"}
        
        response = requests.get(f"{GRAPH_BASE_URL}/me/mailFolders", headers=TOKENS['headers'])
        response.raise_for_status()
        if response.status_code != 200:
            return func.HttpResponse(f"Error obteniendo carpetas: {response.text}", status_code=response.status_code)
        
        folders = response.json()
        for folder in folders.get('value', []):
            if folder.get('displayName') in INBOX_NAMES:
                html = f"""
                <html>
                <body>
                    <h1>Microsoft Graph Authorization</h1>
                    <p>1️⃣ Primer request ejecutado de manera correcta</p>
                </body>
                </html>
                """
                return func.HttpResponse(html, mimetype="text/html", status_code=200)

    except Exception as e:
        logging.error("Error obteniendo token: %s", str(e))
        return func.HttpResponse(f"Error obteniendo token: {str(e)}", status_code=500)
