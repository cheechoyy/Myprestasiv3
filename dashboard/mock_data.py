import random
import pandas as pd
import json
import os

# ==========================================
# 1. FUNGSI JANA DOKTOR (Dinamik & Disatukan)
# ==========================================
def generate_doctors_list(clinic_name):
    # Seed memastikan nama doktor kekal sama untuk klinik tersebut
    random.seed(hash(clinic_name)) 
    doctors = []
    first_names = ["Ahmad", "Siti", "Nur", "Mohammad", "Farhana", "Wei", "Jian", "Mei", "Muthu", "Priya", "Ramesh", "David", "Sarah", "John", "Aisyah", "Kamal", "Raju", "Lim", "Chong", "Fatimah"]
    last_names = ["Ali", "Abu", "Bakar", "Wong", "Tan", "Lee", "Samy", "Nair", "Smith", "Abdullah", "Ismail", "Goh", "Yeoh", "Kumar", "Mansor"]
    
    # 3 hingga 12 doktor setiap klinik
    for _ in range(random.randint(3, 12)):
        score = random.randint(60, 99)
        if score >= 85: status = "🟢 Outstanding"
        elif score >= 75: status = "🟢 Good"
        elif score >= 65: status = "🟡 Competent"
        else: status = "🔴 Needs Attention"
        
        full_name = f"Dr. {random.choice(first_names)} {random.choice(last_names)}"
        doctors.append({
            "Officer Name": f"{full_name} ({random.randint(100,999)})",
            "Department": "Outpatient",
            "Grade": random.choice(["UD41", "UD43", "UD47", "UD51", "UD53", "UD54"]),
            "Current Score": score,
            "Status": status
        })
    return doctors

# ==========================================
# 2. BACA, TAPIS & SATUKAN DATA KLINIK
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE_PATH = os.path.join(BASE_DIR, 'full_mock_klinik.json')

MOCK_KLINIK = []
STATE_CENTERS = {"All States": {"lat": 4.2105, "lon": 101.9758, "zoom": 6}}

try:
    with open(JSON_FILE_PATH, 'r', encoding='utf-8') as f:
        semua_fasiliti = json.load(f)
        
    # TAPISAN 1: Hanya ambil "Klinik" (Buang Hospital & Pejabat Pergigian)
    klinik_am = [
        k for k in semua_fasiliti 
        if "Klinik" in k["name"] 
        and "Hospital" not in k["name"] 
        and "Pergigian" not in k["name"]
    ]
    
    df_klinik = pd.DataFrame(klinik_am)
    senarai_negeri = df_klinik['state'].unique()
    
    # Seed supaya senarai 4-15 klinik ini kekal, tidak bertukar bila refresh page
    random.seed(2026) 
    
    for negeri in senarai_negeri:
        klinik_negeri = df_klinik[df_klinik['state'] == negeri].to_dict('records')
        
        # TAPISAN 2: Pilih 4 hingga 15 klinik sahaja secara rawak untuk setiap negeri
        jumlah_pilihan = min(len(klinik_negeri), random.randint(4, 15))
        klinik_terpilih = random.sample(klinik_negeri, jumlah_pilihan)
        
        for k in klinik_terpilih:
            # GABUNGKAN (Embed): Masukkan senarai doktor terus ke dalam fail/objek klinik ini
            k['doctors'] = generate_doctors_list(k['name'])
            MOCK_KLINIK.append(k)
            
        # KIRA AUTO-CENTER PETA
        purata_lat = sum(k['lat'] for k in klinik_terpilih) / jumlah_pilihan
        purata_lon = sum(k['lon'] for k in klinik_terpilih) / jumlah_pilihan
        zoom_level = 7 if negeri in ["Sabah", "Sarawak"] else 9
        STATE_CENTERS[negeri] = {"lat": purata_lat, "lon": purata_lon, "zoom": zoom_level}

except Exception as e:
    print(f"AMARAN: Fail JSON tidak dijumpai atau ralat. {e}")

# ==========================================
# 3. FUNGSI UNTUK VIEWS.PY AMBIL DATA DOKTOR
# ==========================================
def generate_mock_doctors(clinic_name):
    # Cari klinik tersebut dan pulangkan data doktor yang telah disatukan tadi
    for k in MOCK_KLINIK:
        if k['name'] == clinic_name:
            return pd.DataFrame(k['doctors'])
    return pd.DataFrame([]) # Pulangkan kosong jika tak jumpa

# ==========================================
# 4. DATA MARKAH PROSEDUR (Untuk Graf CUSUM)
# ==========================================
MOCK_CUSUM_DATA = {
    "default": [75, 74, 76, 77, 78, 79, 80, 81, 80, 82, 83, 84, 85, 86, 88, 87, 89, 90, 91, 92]
}