import requests
import re
import pdfplumber
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
#from decode_attachment import decode_and_save_attachment

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
CREDIT_CARD_PATTERN = re.compile(
    r"(\d{6})\s+(\d{2}/\d{2}/\d{4})\s+(.*?)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+-?)\s+([\d,.]+)\s+([\d/]+)"
)
SAVINGS_LINE_PATTERN = re.compile(r"(\d{1,2}/\d{2})\s+(.*?)\s+([\d,.-]+)\s+([\d,.-]+)")
SAVINGS_SUMMARY_PATTERN = re.compile(r"([A-ZÁÉÍÓÚÑ ]+)\s+\$\s+([\d,.]+)")

@dataclass
class CreditCardRecord:
    autorizacion: str
    fecha: str
    descripcion: str
    valor_original: float
    tasa_pactada: str
    tasa_ea: str
    cargos_abonos: float
    saldo_diferir: float
    cuotas: str

@dataclass
class SavingsRecord:
    fecha: str
    descripcion: str
    valor: float
    saldo: float

@dataclass
class InterestRecord:
    fecha: str
    descripcion: str
    valor_original: float
    cargos_abonos: float
    saldo_diferir: float

def safe_float(value: str) -> float:
    """Safely convert string to float, handling commas and negatives."""
    try:
        cleaned = value.replace(",", "").replace("-", "")
        return -float(cleaned) if "-" in value else float(cleaned)
    except ValueError:
        logger.warning(f"Could not convert '{value}' to float")
        return 0.0

def extract_numbers(line: str) -> List[str]:
    numbers = re.findall(r"[\d,.]+", line)
    numbers = [x for x in numbers if x != "," and x != "."]
    return numbers

def parse_credit_card_lines(text_lines: List[str]) -> List[CreditCardRecord]:
    """Parse credit card transaction lines."""
    records = []
    for line in text_lines:
        match = CREDIT_CARD_PATTERN.match(line.strip())
        if match:
            try:
                record = CreditCardRecord(
                    autorizacion=match.group(1),
                    fecha=match.group(2),
                    descripcion=match.group(3).strip(),
                    valor_original=safe_float(match.group(4)),
                    tasa_pactada=match.group(5),
                    tasa_ea=match.group(6),
                    cargos_abonos=safe_float(match.group(7)),
                    saldo_diferir=safe_float(match.group(8)),
                    cuotas=match.group(9),
                )
                records.append(record)
            except Exception as e:
                logger.error(f"Error parsing credit card line: {line}\n{e}")
    return records

def parse_credit_card_summary(text_lines: List[str]) -> Dict[str, Any]:
    """Parse credit card summary information."""
    summary = {}
    total = {}
    minimum = {}
    other_lines = []
    flags = {
        'long_interes': 0,
        'interes_corriente': 0,
        'other_charges': 0,
        'interest_payment': False,
        'service_fee': False,
    }

    for index, line in enumerate(text_lines):
        lower_line = line.lower()

        if "cupo total" in lower_line and index + 1 < len(text_lines):
            values = text_lines[index + 1].split()
            if len(values) >= 8:
                summary['Cupo Total'] = safe_float(values[1])
                summary['Cupo de Avances'] = safe_float(values[3])
                summary['from'] = values[5]
                summary['to'] = values[7]

        elif flags['interest_payment']:
            parts = lower_line.split()
            if len(parts) >= 6:
                record = InterestRecord(
                    fecha=parts[0],
                    descripcion=f"{parts[1]} {parts[2]}",
                    valor_original=safe_float(parts[3]),
                    cargos_abonos=safe_float(parts[4]),
                    saldo_diferir=safe_float(parts[5]),
                )
                other_lines.append(record)
            flags['interest_payment'] = False
            flags['service_fee'] = True

        elif flags['service_fee'] and len(lower_line.split()) == 8:
            parts = lower_line.split()
            record = InterestRecord(
                fecha=parts[1],
                descripcion=f"{parts[2]} {parts[3]} {parts[4]}",
                valor_original=safe_float(parts[5]),
                cargos_abonos=safe_float(parts[6]),
                saldo_diferir=safe_float(parts[7]),
            )
            other_lines.append(record)

        elif "disponible total" in lower_line and index + 1 < len(text_lines):
            values = text_lines[index + 1].split()
            if len(values) >= 5:
                summary['Disponible Total'] = safe_float(values[1])
                summary['Disponible de Avances'] = safe_float(values[3])
                summary['pay before'] = values[4]

        # Summary fields with flags
        summary_fields = [
            ("saldo anterior", total, 'Saldo Anterior', None),
            ("compras del mes", total, 'Compras del mes', None),
            ("intereses de mora", total, 'Intereses de mora', 'long_interes'),
            ("intereses de mora", minimum, 'Intereses de mora', 'long_interes'),
            ("intereses corrientes", total, 'Intereses corrientes', 'interes_corriente'),
            ("intereses corrientes", minimum, 'Intereses corrientes', 'interes_corriente'),
            ("avances", total, 'Avances', None),
            ("cuota avances", minimum, 'Cuota avances', None),
            ("otros cargos", total, 'Otros cargos', 'other_charges'),
            ("otros cargos", minimum, 'Otros cargos', 'other_charges'),
            ("pagos / abonos", total, 'Pagos / abonos', None),
            ("cuota compras anteriores", minimum, 'Cuota compras anteriores', None),
            ("cuota compras del mes", minimum, 'Cuota compras del mes', None),
            ("saldo en mora", minimum, 'Saldo en mora', None),
        ]


        for field in summary_fields:
            key, target, name = field[0], field[1], field[2]
            flag_key = field[3] if len(field) > 3 else None
            if key in lower_line:
                numbers = extract_numbers(line)
                if numbers:
                    if flag_key and flags[flag_key] == 0:
                        target[name] = safe_float(numbers[0])
                        flags[flag_key] = 1
                    elif flag_key and flags[flag_key] == 1:
                        target[name] = safe_float(numbers[0])
                        flags[flag_key] = 0
                    elif not flag_key:
                        target[name] = safe_float(numbers[0])

        if "facturadacargos y abonos saldo a diferir cuotas" in lower_line:
            flags['interest_payment'] = True

    return {
        'summary': summary,
        'total': total,
        'minimum': minimum,
        'other_lines': other_lines,
    }

def parse_credit_card_statement(pdf_path: str, password: str) -> Dict[str, Any]:
    """Parse credit card statement from PDF."""
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            text_lines = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_lines.extend(text.splitlines())

        summary_data = parse_credit_card_summary(text_lines)
        statement_lines = parse_credit_card_lines(text_lines)

        return {
            'Cupo Total': summary_data['summary'].get('Cupo Total'),
            'Cupo de Avances': summary_data['summary'].get('Cupo de Avances'),
            'from': summary_data['summary'].get('from'),
            'to': summary_data['summary'].get('to'),
            'Disponible Total': summary_data['summary'].get('Disponible Total'),
            'Disponible de Avances': summary_data['summary'].get('Disponible de Avances'),
            'pay before': summary_data['summary'].get('pay before'),
            'summary_statement_total': summary_data['total'],
            'summary_statement_minimum': summary_data['minimum'],
            'Statement_lines': summary_data['other_lines'] + statement_lines,
            'type': 'Credit Card',
        }
    except Exception as e:
        logger.error(f"Error parsing credit card PDF {pdf_path}: {e}")
        return {}

def parse_summary_credit(text_lines: List[str]) -> Dict[str, Any]:
    summary = {}
    concepts = ["ABONO A CAPITAL ", "INTERÉS CORRIENTE ", "INTERÉS MORA ", \
            "SEGURO VIDA ", "OTROS CONCEPTOS ", "COMISIÓN FNG/FAG ", \
            "IVA FNG/FAG ", "TOTAL "]
    
    for line in text_lines:
        new_line = line.split(" ")
        concept = ""
        for idx, val in enumerate(new_line):
            concept += val + " "
            if concept in concepts:
                summary[concept[:-1]] = {"previous_payment": new_line[idx + 1]}
                if idx + 2 > len(new_line) - 1:
                    summary[concept[:-1]]["payment"] = 0
                else:
                    summary[concept[:-1]]["payment"] = new_line[idx + 2]
    return summary

def parse_credit_information(text_lines: List[str]) -> Dict[str, Any]:
    credit_information = {}
    concepts = ["FECHA DE DESEMBOLSO", "VALOR INICIAL", "FECHA CORTE EXTRACTO", 
            "SALDO DE CAPITAL", "TASA DE INTERÉS E.A.", "CUOTA NÚMERO",
            "TASA MORA A LA FECHA", "SALDO EN MORA CAPITAL",
            "Nº DE CUOTAS EN MORA", "FECHA ÚLTIMO PAGO", "MORA DESDE"]
    for line in text_lines:
        new_line = line.split(" ")
        concept = ""
        for idx in range(len(new_line), -1, -1):
            concept = " ".join(new_line[idx:-1])
            if concept in concepts:
                credit_information[concept] = new_line[-1]
    
    lacks = [key for key in concepts if key not in credit_information.keys()]
    for lack in lacks:
        credit_information[lack] = ''

    return credit_information

def parse_credit_statement(pdf_path: str, password: str) -> Dict[str, Any]:
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            text_lines = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_lines.extend(text.splitlines())
        
        summary = parse_summary_credit(text_lines)
        credit_information = parse_credit_information(text_lines)
        credit_statement = {"credit_information": credit_information,
                             "summary": summary}

        for idx, val in enumerate(text_lines):
            if val == 'SALDO DE CRÉDITO':
                credit_statement[val] = text_lines[idx + 1]
            if "FECHA DE PAGO" in val:
                credit_statement['date'] = val.split(" ")[-1]
        
        return credit_statement
      
    except Exception as e:
        logger.error(f"Error parsing credit card PDF {pdf_path}: {e}")
        return {}


def parse_savings_lines(text_lines: List[str]) -> Dict[str, Any]:
    """Parse savings account statement."""
    lines = []
    summary = {}
    statement_info = {}

    for line in text_lines:
        stripped = line.strip()
        match_line = SAVINGS_LINE_PATTERN.match(stripped)
        matches_summary = SAVINGS_SUMMARY_PATTERN.findall(stripped)

        if 'DESDE' in line:
            parts = line.split()
            if len(parts) >= 4:
                statement_info['from'] = parts[1]
                statement_info['to'] = parts[3]
        elif 'NÚMERO' in line:
            parts = line.split()
            if len(parts) >= 2:
                statement_info['number'] = parts[1]
        elif match_line:
            try:
                record = SavingsRecord(
                    fecha=match_line.group(1),
                    descripcion=match_line.group(2).strip(),
                    valor=safe_float(match_line.group(3)),
                    saldo=safe_float(match_line.group(4)),
                )
                lines.append(record)
            except Exception as e:
                logger.error(f"Error parsing savings line: {line}\n{e}")
        elif matches_summary:
            try:
                for campo, valor in matches_summary:
                    summary[campo.strip()] = safe_float(valor)
            except Exception as e:
                logger.error(f"Error parsing savings summary: {line}\n{e}")

    return {
        'from': statement_info.get('from'),
        'to': statement_info.get('to'),
        'number': statement_info.get('number'),
        'summary_statement_total': summary,
        'Statement_lines': lines,
        'type': 'Savings Account',
    }

def parse_savings_statement(pdf_path: str, password: str) -> Dict[str, Any]:
    """Parse savings statement from PDF."""
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            text_lines = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_lines.extend(text.splitlines())

        return parse_savings_lines(text_lines)
    except Exception as e:
        logger.error(f"Error parsing savings PDF {pdf_path}: {e}")
        return {}