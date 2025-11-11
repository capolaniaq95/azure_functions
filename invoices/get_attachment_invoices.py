import azure.functions as func
import requests
from invoices.decode_attachment import decode_and_save_attachment
from invoices.extract_invoice_attachment import get_from_attachment, extract_invoice_from_pdf
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO)

def extract_invoice(id: str, headers: dict):
    """
    Extract data from invoices emails, depend of the electronic invoice, if have a PDF file, or 
    a zip file
    """
    logging.info(f"Extracting invoice from email with id: {id}")

    path = f"https://graph.microsoft.com/v1.0/me/messages/{id}/attachments"
    response = requests.get(path, headers=headers)
    if response.status_code == 200:
        
        attachments = response.json()['value']
        
        for attachment in attachments:
            attachment_name = attachment['name']
            attachment_encode = attachment['contentBytes']
            if (attachment['contentType'] == "application/zip" or attachment['contentType'] == "application/octet-stream") and attachment_name[-3:] == "zip":
                path_attachment = "/tmp/" + attachment_name
                decode_and_save_attachment(attachment_encode, path_attachment)
                info = get_from_attachment(path_attachment=path_attachment)
                return info
            elif (attachment['contentType'] == 'application/pdf' or attachment['contentType'] == "application/octet-stream") and attachment_name[-3:] == "pdf":
                path_attachment = "/tmp/" + attachment_name
                decode_and_save_attachment(attachment_encode, path_attachment)
                password = "1026291584" # the password is  temporally None, but in other cases is the Identification of user
                info = extract_invoice_from_pdf(path_attachment, password)
            
                return info