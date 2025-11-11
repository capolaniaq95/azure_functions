import azure.functions as func
import requests
import logging
from msal import PublicClientApplication
import json
from datetime import datetime
from notifications.extract_notificacions import extract_notification_email
from payments.extract_payments import get_html_payment
from invoices.extract_invoice_attachment import extract_enel_invoice
from invoices.get_attachment_invoices import extract_invoice
from keys import *


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="auth")
@app.queue_output(arg_name="queue_device_flow", 
                  queue_name="auth-state-queue", 
                  connection="AzureWebJobsStorage")
def auth(req: func.HttpRequest, queue_device_flow: func.Out[str]) -> func.HttpResponse:
    logging.info('Auth route triggered.')

    try:
        msal_app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)


        device_flow = msal_app.initiate_device_flow(scopes=SCOPES)
        if 'user_code' not in device_flow:
            return func.HttpResponse("Error al iniciar el flujo de dispositivo.", status_code=500)

        html = f"""
        <html>
        <body>
            <h1>Microsoft Graph Authorization</h1>
            <p>1️⃣ Ve a este enlace para autorizar:</p>
            <a href="{device_flow['verification_uri']}">{device_flow['verification_uri']}</a>
            <p>2️⃣ Escribe el código: <b>{device_flow['user_code']}</b></p>
        </body>
        </html>
        """
        serializable_flow = {k: v for k, v in device_flow.items() if not k.startswith('_')}
        queue_device_flow.set(json.dumps(serializable_flow))

        return func.HttpResponse(html, mimetype="text/html", status_code=200)

    except Exception as e:
        logging.error("Error durante autenticación: %s", str(e))
        return func.HttpResponse(f"Error durante autenticación: {str(e)}", status_code=500)


@app.function_name(name="get_messages")
@app.queue_trigger(arg_name="queue_device_flow", queue_name="auth-state-queue",
                   connection="AzureWebJobsStorage")
@app.blob_output(arg_name="notificationsBlob",
                 path="messages/notifications.json",
                 connection="AzureWebJobsStorage")
@app.blob_output(arg_name="invoicesBlob",
                 path="messages/invoices.json",
                 connection="AzureWebJobsStorage")
@app.blob_output(arg_name="statementsBlob",
                 path="messages/statements.json",
                 connection="AzureWebJobsStorage")
@app.blob_output(arg_name="paymentsBlob",
                 path="messages/payments.json",
                 connection="AzureWebJobsStorage")
@app.queue_output(arg_name="notifications_queue", queue_name="notifications-queue",
                  connection="AzureWebJobsStorage")
@app.queue_output(arg_name="invoices_queue", queue_name="invoices-queue",
                  connection="AzureWebJobsStorage")
@app.queue_output(arg_name="statements_queue", queue_name="statements-queue",
                  connection="AzureWebJobsStorage")
@app.queue_output(arg_name="payments_queue", queue_name="payments-queue",
                  connection="AzureWebJobsStorage")
def get_messages(queue_device_flow: func.QueueMessage, 
                notificationsBlob: func.Out[str],
                invoicesBlob: func.Out[str],
                statementsBlob: func.Out[str],
                paymentsBlob: func.Out[str],
                notifications_queue: func.Out[str], 
                invoices_queue: func.Out[str],
                payments_queue: func.Out[str],
                statements_queue: func.Out[str]) -> None:
    logging.info("Intentando obtener carpetas de correo...")
    today = datetime.today().strftime('%Y-%m-%d')
    try:
        msal_app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
        body = queue_device_flow.get_body().decode('utf-8')
        TOKENS['device_flow'] = json.loads(body)
        result = msal_app.acquire_token_by_device_flow(TOKENS['device_flow'])

        TOKENS['access_token'] = result['access_token']
        TOKENS['headers'] = {"Authorization": f"Bearer {TOKENS['access_token']}"}
        
        response = requests.get(f"{GRAPH_BASE_URL}/me/mailFolders", headers=TOKENS['headers'])
        response.raise_for_status()
        if response.status_code != 200:
            logging.error(f"Error obteniendo carpetas: {response.text}")
            return
        
        folders = response.json()
        for folder in folders.get('value', []):
            if folder.get('displayName') in INBOX_NAMES:
                inbox_id = folder.get('id')
        
        if not inbox_id:
            logging.warning("No se encontró la carpeta de entrada.")
            return
    
        logging.info(f"Carpeta de entrada ID: {inbox_id}")

        path = f"{GRAPH_BASE_URL}/me/mailFolders/{inbox_id}/messages?$top={MESSAGE_LIMIT}"
        response = requests.get(path, headers=TOKENS['headers'])
        response.raise_for_status()
        messages_data = response.json()
        messages = messages_data.get('value', [])
        if len(messages) == 0:
            logging.info("No se encontraron mensajes en la bandeja de entrada.")
            return
        
        logging.info(f"Se encontraron {len(messages)} mensajes en la bandeja de entrada.")

        invoices_data = []
        notifications_data = []
        statetmens_data = []
        payments_data = []
        for message in messages:
            msg_id = message.get('id')
            subject = message.get('subject')
            body_preview = message.get('bodyPreview')
            attachments = message.get('hasAttachments')
            content = message.get('body', {}).get('content')
            sender = message.get('sender', {}).get('emailAddress', {}).get('address')
        
            if subject == 'Alertas y Notificaciones' or 'Alertas y Notificaciones' in body_preview:
                msg_type =  'notification'
            elif (any(kw in body_preview for kw in PAYMENT_KEYWORDS) or
                any(kw in subject for kw in PAYMENT_KEYWORDS)) and not any(kw in sender for kw in INVOICE_KEYWORDS):
                msg_type = 'payment'
            elif any(kw in body_preview for kw in EXTRACT_KEYWORDS) or any(kw in subject for kw in EXTRACT_KEYWORDS):
                msg_type = 'extract'
            elif any(kw in body_preview for kw in INVOICE_KEYWORDS) or any(kw in subject for kw in INVOICE_KEYWORDS):
                msg_type = 'invoice'
            else:
                msg_type = 'other'
            
            msg_data = {
                'id': msg_id,
                'subject': subject,
                'body': body_preview,
                'attachments': attachments,
                'sender': sender,
                'type': msg_type,
                'content': content
            }    

            if msg_type == 'notification':
                notifications_data.append(msg_data)
            elif msg_type == 'payment':
                payments_data.append(msg_data)
            elif msg_type == 'extract':
                statetmens_data.append(msg_data)
            elif msg_type == 'invoice':
                invoices_data.append(msg_data)

            logging.info(f"Procesado mensaje ID: {msg_id}, Tipo: {msg_type}")
        
        notificationsBlob.set(json.dumps(notifications_data))
        notifications_queue.set(json.dumps(TOKENS['headers']))
        logging.info("Cola de notificaciones actualizada.")

        invoicesBlob.set(json.dumps(invoices_data))
        invoices_queue.set(json.dumps(TOKENS['headers']))
        logging.info("Cola de facturas actualizada.")

        statementsBlob.set(json.dumps(statetmens_data))
        statements_queue.set(json.dumps(TOKENS['headers']))
        logging.info("Cola de extractos actualizada.")

        paymentsBlob.set(json.dumps(payments_data))
        payments_queue.set(json.dumps(TOKENS['headers']))
        logging.info("Cola de pagos actualizada.")
        
    except Exception as e:
        logging.error("Error obteniendo token: %s", str(e))
        return


@app.function_name(name="extract_notifications")
@app.queue_trigger(arg_name="notifications_queue", queue_name="notifications-queue",
                   connection="AzureWebJobsStorage")
@app.blob_input(arg_name="inputBlob",
                path="messages/notifications.json",
                connection="AzureWebJobsStorage")
@app.blob_output(arg_name="outputBlob",
                 path="processed-data/extracted_notifications_{datetime}.json",
                 connection="AzureWebJobsStorage")
def extract_notifications(notifications_queue: func.QueueMessage, inputBlob: str, outputBlob: func.Out[str]) -> None:
    logging.info("Procesando notificaciones...")
    try:
        today = datetime.today().strftime('%Y-%m-%d')
        headers = json.loads(notifications_queue.get_body().decode('utf-8'))
        logging.info(f"Headers recibidos para extracción: {headers}")

        notifications_data = json.loads(inputBlob)
        logging.info(f"Número de notificaciones a procesar: {len(notifications_data)}")
        
        extracted_data = []
        for msg in notifications_data:
            body_preview = msg.get('body', '')
            if body_preview:
                extracted = extract_notification_email(body_preview)
                extracted['id'] = msg.get('id')
                extracted['subject'] = msg.get('subject')
                extracted_data.append(extracted)
        
        outputBlob.set(json.dumps(extracted_data))
        logging.info(f"Extraídas {len(extracted_data)} notificaciones.")
    except Exception as e:
        logging.error(f"Error procesando notificaciones: {str(e)}")


@app.function_name(name="extract_payments")
@app.queue_trigger(arg_name="payments_queue", queue_name="payments-queue",
                   connection="AzureWebJobsStorage")
@app.blob_input(arg_name="inputBlob",
                path="messages/payments.json",
                connection="AzureWebJobsStorage")
@app.blob_output(arg_name="outputBlob",
                 path="processed-data/extracted_payments_{datetime}.json",
                 connection="AzureWebJobsStorage")
def extract_payments(payments_queue: func.QueueMessage, inputBlob: str, 
                     outputBlob: func.Out[str]) -> None:
    """
        Function to extract payment information from email messages.
    Args:
        payments_queue (func.QueueMessage): Queue message containing headers for authentication.
        inputBlob (str): Blob input containing raw email messages in JSON format.
        outputBlob (func.Out[str]): Blob output to store extracted payment information in JSON format.
    Returns:
        None
    """
    logging.info("Procesando pagos...")
    try:
        headers = json.loads(payments_queue.get_body().decode('utf-8'))
        payments_data = json.loads(inputBlob)
        logging.info(f"Número de pagos a procesar: {len(payments_data)}")
        extracted_data = []
        for msg in payments_data:
            id = msg.get('id')
            info = get_html_payment(id, headers)
            if info:
                logging.info(f"Pago extraído para mensaje ID: {id}")
                extracted_data.append(info)
        
        outputBlob.set(json.dumps(extracted_data))
        logging.info(f"Extraídos {len(extracted_data)} pagos.")
    except Exception as e:
        logging.error(f"Error procesando pagos: {str(e)}")


@app.function_name(name="extract_invoices")
@app.queue_trigger(arg_name="invoices_queue", queue_name="invoices-queue",
                   connection="AzureWebJobsStorage")
@app.blob_input(arg_name="inputBlob",
                path="messages/invoices.json",
                connection="AzureWebJobsStorage")
@app.blob_output(arg_name="outputBlob",
                 path="processed-data/extracted_invoices_{datetime}.json",
                 connection="AzureWebJobsStorage")
@app.blob_output(arg_name="attachmentsBlob",
                 path="raw-data/invoice_attachments_{datetime}",
                 connection="AzureWebJobsStorage")
def extract_invoices(invoices_queue: func.QueueMessage, inputBlob: str, 
                     outputBlob: func.Out[str], attachmentsBlob: func.Out[str]) -> None:
    """
        Function to extract invoice information from email messages.
    Args:
        invoices_queue (func.QueueMessage): Queue message containing headers for authentication.
        inputBlob (str): Blob input containing raw email messages in JSON format.
        outputBlob (func.Out[str]): Blob output to store extracted invoice information in JSON format.
        attachmentsBlob (func.Out[str]): Blob output to store raw attachments.
    Returns:
        None
    """
    logging.info("Procesando facturas...")
    try:
        headers = json.loads(invoices_queue.get_body().decode('utf-8'))
        invoices_data = json.loads(inputBlob)
        logging.info(f"Número de facturas a procesar: {len(invoices_data)}")
        extracted_data = []
        for msg in invoices_data:
            id = msg.get('id')
            subject = msg.get('subject')
            attachments = msg.get('attachments', False)
            if 'Enel Colombia' in subject:
                info = extract_enel_invoice(id, headers)
            elif attachments:
                info = extract_invoice(id, headers)
                info.update({'subject': subject, 'ID_email': id})
            extracted_data.append(info)
        
        outputBlob.set(json.dumps(extracted_data))
        logging.info(f"Extraídas {len(extracted_data)} facturas.")
    except Exception as e:
        logging.error(f"Error procesando facturas: {str(e)}")
