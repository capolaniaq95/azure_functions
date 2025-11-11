import azure.functions as func
import base64
import zipfile
import os
import logging

def decode_and_save_attachment(encoded_attachment:str, path_attachment:str):
    """
    Decodifica un attachment en Base64 y lo guarda como un archivo ZIP.

    :param encoded_attachment: El contenido del attachment codificado en Base64.
    :param output_zip_path: Ruta donde se guardará el archivo ZIP decodificado.
    """
    logging.info("Decodificando y guardando el attachment...")
    
    try:
        decoded_data = base64.b64decode(encoded_attachment)
        
        with open(path_attachment, "wb") as file:
            file.write(decoded_data)

        return True
    except Exception as e:
        print(f"Error al decodificar el attachment: {e}")
        return False
