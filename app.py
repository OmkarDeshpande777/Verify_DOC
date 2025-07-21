from flask import Flask, request, jsonify, render_template
import os
import uuid
import re
from PIL import Image
from pdf2image import convert_from_path
import cv2
import numpy as np
import pytesseract
from rapidfuzz import fuzz, process as rapid_process
import base64
import json
from hashlib import sha256
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime

# Configure Tesseract path
from flask import Flask, request, jsonify, render_template
import os
import uuid
import re
from PIL import Image
from pdf2image import convert_from_path
import cv2
import numpy as np
import pytesseract
from rapidfuzz import fuzz, process as rapid_process
import base64
import json
from hashlib import sha256
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime

# Configure Tesseract path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# API_KEYS_FILE = 'api_keys.json'

# def load_api_keys():
#     if not os.path.exists(API_KEYS_FILE):
#         return {}
#     with open(API_KEYS_FILE, 'r') as f:
#         return json.load(f)

# def save_api_keys(keys):
#     with open(API_KEYS_FILE, 'w') as f:
#         json.dump(keys, f, indent=2)

def generate_api_key(email):
    raw = f"{email}-{uuid.uuid4()}"
    return sha256(raw.encode()).hexdigest()


# MongoDB setup
client = MongoClient("mongodb://localhost:27017/")  # or MongoDB Atlas URI
db = client["doc_verifier"]
api_keys_collection = db["api_keys"]


app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

document_keywords = {
    "Aadhar Card": ["uidai", "unique identification authority of india", "government of india", "aadhaar"],
    "PAN Card": ["income tax department", "permanent account number", "pan", "govt of india"],
    "Handicap smart card": ["smart card", "disability", "handicap card", "govt issued"],
    "Birth Certificate": ["birth certificate", "date of birth", "place of birth"],
    "Bonafide Certificate": ["bonafide", "student", "institution", "studying", "enrolled"],
    "Caste certificate": ["caste", "category", "scheduled caste", "scheduled tribe", "other backward class"],
    "Current Month Salary Slip": ["salary", "monthly pay", "employee code", "basic pay"],
    "Passport and VISA": ["passport", "visa", "republic of india", "expiry date"],
    "Marksheet": ["marks", "subject", "grade", "exam", "percentage", "semester"],
    "Transgender Certificate": ["transgender", "gender identity", "third gender"]
}

# Required fields per document for fuzzy matching
DOCUMENT_FIELDS = {
    "Aadhar Card": ["aadhar_number", "name", "dob", "address"],
    "PAN Card": ["DOB", "Name", "Pan Number"],
    "Transgender Certificate": ["Identity card number", "Name", "Gender", "Identity card reference number"],
    "Caste certificate": ["Name", "Caste", "Caste-Category"],
    "Marksheet": ["name", "roll_number", "percentage"],
    "Bonafide Certificate": ["college_name", "student_name", "class", "academic_year"],
    "Birth Certificate": ["Name", "Date"],
    "Passport and VISA": ["Name", "Date Of Expiry"],
    "Current Month Salary Slip": ["EMPNO", "EMPName", "Designation"],
    "Handicap smart card":["Name", "UDI_ID", "Disability_Type", "Disability%"]
}

DOC_MODEL_PATHS = {
    "Aadhar Card": os.path.join("models", "aadhaar.pt"),
    "PAN Card": os.path.join("models", "pan_best.pt"),
    "Handicap smart card": os.path.join("models", "handicap_smart_card.pt"),
    "Birth Certificate": os.path.join("models", "Birth_certificatebest.pt"),
    "Bonafide Certificate": None,
    "Caste certificate": os.path.join("models", "caste_certificate.pt"),
    "Current Month Salary Slip": os.path.join("models", "salaryslipbest.pt"),
    "Passport and VISA": os.path.join("models", "passport.pt"),
    "Marksheet": None,
    "Transgender Certificate": os.path.join("models", "trans_best.pt")
}

def extract_bonafide_fields(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    text = pytesseract.image_to_string(gray, config='--oem 3 --psm 6')

    fields = {
        "college_name": None,
        "student_name": None,
        "class": None,
        "academic_year": None
    }

    college_match = re.search(r'(?i)([A-Z ]+LAW COLLEGE)', text)
    if college_match:
        fields['college_name'] = college_match.group(1).title().strip()

    name_match = re.search(r'This is to certify that\s+(?:KU\.?\s+)?([A-Z ]+?)\s+is/was', text)
    if name_match:
        fields['student_name'] = name_match.group(1).title().strip()

    class_match = re.search(r'class\s+([A-Z\s]+\d+)', text)
    if class_match:
        fields['class'] = class_match.group(1).strip()

    year_match = re.search(r'academic year\s+([0-9]{4}-[0-9]{4})', text)
    if year_match:
        fields['academic_year'] = year_match.group(1).strip()

    return fields


def extract_marksheet_fields(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    text = pytesseract.image_to_string(gray, config='--oem 3 --psm 6')

    fields = {
        "student_name": None,
        "roll_number": None,
        "percentage": None
    }

    keyword_map = {
        "name of the student": "student_name",
        "roll no": "roll_number",
        "percentage": "percentage"
    }

    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]

    for line in lines:
        for keyword, field in keyword_map.items():
            if keyword in line:
                try:
                    after = line.split(keyword)[1]
                    after = after.strip(" :.-").strip()
                    fields[field] = after
                except:
                    fields[field] = "not_found"

    return fields


def process_with_regex(image, document_type):
    fields = {}
    annotated_image = image.copy()

    if document_type == "Bonafide Certificate":
        fields = extract_bonafide_fields(image)
    elif document_type == "Marksheet":
        fields = extract_marksheet_fields(image)
    else:
        # fallback generic OCR
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(gray, config='--oem 3 --psm 6')
        fields = {
            "extracted_text": text.strip(),
            "document_type": document_type
        }

    return fields, annotated_image


def classify_document(image_path):
    try:
        if not os.path.exists(image_path):
            print(f"Error in classify_document: File not found: {image_path}")
            return "Unknown Document", 0
            
        image = Image.open(image_path)
        try:
            text = pytesseract.image_to_string(image, lang='eng')
            text = text.lower()
        except Exception as ocr_error:
            print(f"OCR Error in classify_document: {ocr_error}")
            # Fall back to empty text if OCR fails
            text = ""
            
        scores = {}
        for doc_type, keywords in document_keywords.items():
            score = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text))
            scores[doc_type] = score
            
        if not scores:
            return "Unknown Document", 0
            
        best_match = max(scores, key=scores.get)
        best_score = scores[best_match]
        if best_score > 0:
            return best_match, best_score
        else:
            return "Unknown Document", 0
    except PermissionError as pe:
        print(f"Permission Error in classify_document: {pe} - Check if the file is accessible")
        return "Unknown Document", 0
    except Exception as e:
        print(f"Error in classify_document: {str(e)}, Type: {type(e).__name__}")
        return "Unknown Document", 0

def load_image(file_path):
    try:
        ext = file_path.split('.')[-1].lower()
        if ext == 'pdf':
            images = convert_from_path(file_path)
            return cv2.cvtColor(np.array(images[0]), cv2.COLOR_RGB2BGR)
        else:
            return cv2.cvtColor(np.array(Image.open(file_path).convert("RGB")), cv2.COLOR_RGB2BGR)
    except Exception as e:
        print(f"Error in load_image: {e}")
        # Return a blank image as fallback
        return np.zeros((100, 100, 3), dtype=np.uint8)

# def extract_name_from_text(text):
#     # Simple regex for name extraction, can be improved per document type
#     match = re.search(r'[:\s]+([A-Z][a-zA-Z\s]+)', text, re.IGNORECASE)
#     if match:
#         return match.group(1).strip()
#     # fallback: first line with more than 2 words
#     for line in text.splitlines():
#         if len(line.split()) > 2:
#             return line.strip()
#     return ""

def extract_name_from_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    probable_names = []

    # Heuristic: lines with 2–4 capitalized words, likely to be names
    for line in lines:
        words = line.split()
        if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w.isalpha()):
            probable_names.append(line)

    # Try to find name before DOB or Gender (most common structure)
    dob_keywords = ["dob", "date of birth"]
    gender_keywords = ["male", "female", "other"]

    dob_idx = -1
    for i, line in enumerate(lines):
        if any(k in line.lower() for k in dob_keywords):
            dob_idx = i
            break

    if dob_idx > 0:
        for i in range(dob_idx - 2, -1, -1):
            if lines[i] in probable_names:
                return lines[i]

    # Else: return the most "alphabet-heavy" probable name
    best = max(probable_names, key=lambda x: sum(c.isalpha() for c in x), default="")
    return best


def process_document(file_path, doc_type):
    image = load_image(file_path)
    model_path = DOC_MODEL_PATHS.get(doc_type)
    if model_path and os.path.exists(model_path):
        fields, annotated_image = run_yolo_ocr(image, model_path)
        # Try to extract name from YOLO fields
        extracted_name = ""
        for k, v in fields.items():
            if "name" in k.lower():
                extracted_name = v
                break
        if not extracted_name:
            # fallback to OCR
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            text = pytesseract.image_to_string(gray, config='--oem 3 --psm 6')
            extracted_name = extract_name_from_text(text)
        # Format all fields as key-value pairs
        raw_text = "\n".join([f"{k}: {v}" for k, v in fields.items()])
        return {
            "extracted_name": extracted_name,
            "raw_text": raw_text,
            "fields": fields,
            "annotated_image": image_to_base64(annotated_image)
        }
    else:
        # For regex-based documents
        if doc_type in ["Bonafide Certificate", "Marksheet"]:
            fields, annotated_image = process_with_regex(image, doc_type)
            extracted_name = fields.get("student_name") or extract_name_from_text(pytesseract.image_to_string(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)))
            raw_text = "\n".join([f"{k}: {v}" for k, v in fields.items()])
            return {
                "extracted_name": extracted_name,
                "raw_text": raw_text,
                "fields": fields,
                "annotated_image": image_to_base64(annotated_image)
            }
        else:
            # Generic fallback
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            text = pytesseract.image_to_string(gray, config='--oem 3 --psm 6')
            name = extract_name_from_text(text)
            return {
                "extracted_name": name,
                "raw_text": text,
                "fields": {},
                "annotated_image": None
            }


def normalize_name(name):
    if not name:
        return ""
    # Lowercase, remove punctuation, collapse spaces
    name = name.lower()
    name = re.sub(r'[^a-z0-9 ]+', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def fuzzy_match_name(extracted_name, user_name):
    if not extracted_name or not user_name:
        return 0
    name1 = normalize_name(extracted_name)
    name2 = normalize_name(user_name)
    # Use multiple matchers for robustness
    scores = [
        fuzz.token_set_ratio(name1, name2),
        fuzz.partial_ratio(name1, name2),
        fuzz.ratio(name1, name2)
    ]
    return max(scores)

def image_to_base64(image):
    if image is None:
        return None
    _, buffer = cv2.imencode('.jpg', image)
    img_str = base64.b64encode(buffer).decode('utf-8')
    return img_str

def run_yolo_ocr(image, model_path):
    try:
        from ultralytics import YOLO
        model = YOLO(model_path)
        results = model(image)[0]
        fields = {}
        image_drawn = image.copy()
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        for i, box in enumerate(results.boxes):
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cls_id = int(box.cls[0])
            class_name = results.names[cls_id]
            text = pytesseract.image_to_string(Image.fromarray(image_rgb[y1:y2, x1:x2]), config='--psm 6').strip()
            fields[class_name] = text if text else "not_verified"
            cv2.rectangle(image_drawn, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(image_drawn, class_name, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        return fields, image_drawn
    except Exception as e:
        print(f"Error in run_yolo_ocr: {e}")
        return {}, image

@app.route('/document_fields/<doc_type>')
def get_document_fields(doc_type):
    print(f"[DEBUG] /document_fields called with doc_type: '{doc_type}'")
    # Return the required fields for a given document type
    fields = DOCUMENT_FIELDS.get(doc_type, [])
    return jsonify({"fields": fields})

@app.route('/')
def index():
    return render_template('index.html', doc_types=list(document_keywords.keys()))

@app.route('/enterprise')
def enterprise():
    return render_template('enterprise.html', doc_types=list(document_keywords.keys()))

@app.route('/classify_document', methods=['POST'])
def classify_document_api():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['file']
        file_id = str(uuid.uuid4())
        file_extension = file.filename.split('.')[-1].lower()
        file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.{file_extension}")
        file.save(file_path)
        doc_type, confidence = classify_document(file_path)
        os.remove(file_path)
        return jsonify({'document_type': doc_type, 'confidence': confidence})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/process_documents', methods=['POST'])
def process_documents_api():
    try:
        print("[INFO] Process documents request received")
        # Determine where request is coming from (Referer or Origin)
        referer = request.headers.get("Referer", "") or request.headers.get("Origin", "")
        print(f"[DEBUG] Request referer: {referer}")
        require_api_key = "enterprise" in referer.lower()

        # If enterprise.html is being used, enforce API Key validation
        if require_api_key:
            api_key = request.headers.get("X-API-Key")
            if not api_key:
                print("[ERROR] Missing API key")
                return jsonify({'error': 'Missing API key'}), 403
            key_entry = api_keys_collection.find_one({"key": api_key})
            if not key_entry:
                print("[ERROR] Invalid API key")
                return jsonify({'error': 'Invalid API key'}), 403
            print("[INFO] Valid API key provided")
        else:
            print("[PUBLIC DEMO] Request from index.html or external client. No API key required.")

        # Process uploaded documents
        if 'files' not in request.files:
            print("[ERROR] No files in request")
            return jsonify({'error': 'No files uploaded'}), 400
            
        files = request.files.getlist('files')
        if not files:
            print("[ERROR] Empty files list")
            return jsonify({'error': 'No files uploaded'}), 400
            
        print(f"[INFO] Processing {len(files)} files")
        user_name = request.form.get('user_name', '').strip()
        confirmed_types = request.form.getlist('confirmed_types')
        results = []

        for idx, file in enumerate(files):
            file_path = None
            try:
                print(f"[INFO] Processing file {idx+1}/{len(files)}: {file.filename}")
                
                file_id = str(uuid.uuid4())
                file_extension = file.filename.split('.')[-1].lower()
                file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.{file_extension}")
                
                # Ensure the uploads directory exists
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                
                # Save the file
                file.save(file_path)
                print(f"[DEBUG] File saved to: {file_path}")
                
                # Determine document type
                if idx < len(confirmed_types) and confirmed_types[idx]:
                    doc_type = confirmed_types[idx]
                    confidence = None
                    print(f"[DEBUG] Using confirmed type: {doc_type}")
                else:
                    print("[DEBUG] Classifying document...")
                    doc_type, confidence = classify_document(file_path)
                    print(f"[DEBUG] Classified as: {doc_type} with confidence: {confidence}")

                # Process the document
                print(f"[DEBUG] Processing document of type: {doc_type}")
                doc_info = process_document(file_path, doc_type)
                extracted_fields = doc_info.get("fields", {})
            except Exception as e:
                print(f"[ERROR] Failed to process file {file.filename}: {str(e)}")
                # Add error result
                results.append({
                    "filename": file.filename,
                    "error": str(e),
                    "doc_type": "Error",
                    "raw_text": f"Error processing file: {str(e)}",
                    "fields": {},
                    "annotated_image": None
                })
                # Skip to next file
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
                continue
            # For backward compatibility, also include extracted_name
            extracted_name = doc_info.get("extracted_name", "")

            # Get required fields for this document type
            required_fields = DOCUMENT_FIELDS.get(doc_type, [])
            match_scores = {}
            match_results = {}
            user_fields = {}

            for field in required_fields:
                # User input field name in form: fields_{idx}_{field}
                form_key = f"fields_{idx}_{field}"
                user_value = request.form.get(form_key, "").strip()
                user_fields[field] = user_value

                # Try to get extracted value from extracted_fields, fallback to extracted_name for "name"
                extracted_value = ""
                # Try several possible keys for "name" field
                if field.lower() == "name":
                    # Try "name", "student_name", "extracted_name"
                    extracted_value = (
                        extracted_fields.get("name") or
                        extracted_fields.get("student_name") or
                        extracted_name
                    )
                else:
                    extracted_value = extracted_fields.get(field, "")

                # Compute fuzzy score
                score = fuzzy_match_name(extracted_value, user_value)
                match_scores[field] = score
                match_results[field] = "pass" if score >= 80 else "fail"

                # Logging for validation
                print(f"[DEBUG] File: {file.filename}, Field: {field}, User: '{user_value}', Extracted: '{extracted_value}', Score: {score}")

            results.append({
                "filename": file.filename,
                "doc_type": doc_type,
                "confidence": confidence,
                "extracted_name": extracted_name,
                "user_name": user_name,
                "match_scores": match_scores,
                "match_results": match_results,
                "raw_text": doc_info["raw_text"],
                "fields": extracted_fields,
                "annotated_image": doc_info.get("annotated_image", None)
            })

            os.remove(file_path)

        return jsonify({"results": results})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    
@app.route('/generate_api_key', methods=['POST'])
def generate_key():
    data = request.get_json()
    email = data.get("email")
    company = data.get("company", "Unknown")

    if not email:
        return jsonify({"error": "Email is required"}), 400

    # Check if key already exists
    existing = api_keys_collection.find_one({"email": email})
    if existing:
        return jsonify({
            "api_key": existing["key"],
            "message": "Key already exists"
        })

    new_key = generate_api_key(email)
    api_keys_collection.insert_one({
        "email": email,
        "company": company,
        "key": new_key,
        "created_at": datetime.utcnow()
    })

    return jsonify({"api_key": new_key})


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
