from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings
import random
import json
import os
from datetime import datetime, timedelta

class Command(BaseCommand):
    help = 'Menjana Data 150+ Klinik Mewakili Seluruh Negeri Malaysia'

    def handle(self, *args, **kwargs):
        self.stdout.write("Membaca fail JSON dan menyusun klinik mengikut negeri...")

        # 1. BACA FAIL JSON KLINIK SEBENAR
        # Pastikan fail full_mock_klinik.json berada sama level dengan manage.py
        json_path = os.path.join(settings.BASE_DIR, 'full_mock_klinik.json')
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                semua_klinik = json.load(f)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR("Ralat: Fail full_mock_klinik.json tidak dijumpai di folder utama!"))
            return

        # 2. SUSUN KLINIK MENGIKUT NEGERI DAN PILIH 150+ KLINIK
        clinics_by_state = {}
        for k in semua_klinik:
            state = k.get('state', 'Unknown')
            if state not in clinics_by_state:
                clinics_by_state[state] = []
            clinics_by_state[state].append(k)

        klinik_terpilih = []
        # Ambil lebih kurang 10-15 klinik untuk setiap negeri
        for state, clinics in clinics_by_state.items():
            num_to_pick = min(len(clinics), random.randint(10, 15)) 
            klinik_terpilih.extend(random.sample(clinics, num_to_pick))

        self.stdout.write(self.style.WARNING(f"Jumlah Klinik Terpilih: {len(klinik_terpilih)} buah dari {len(clinics_by_state)} Negeri/Wilayah."))
        self.stdout.write("Membersihkan database lama dan menjana data baru. Sila tunggu (Mungkin ambil masa 10-30 saat)...")

        first_names = ["Ahmad", "Siti", "Nur", "Mohammad", "Wei Jian", "Mei Ling", "Muthu", "Priya", "Ramesh", "Sarah", "Aisyah", "Kamal", "Chong", "Fatimah", "Ali", "Abu"]
        last_names = ["Ali", "Abu", "Bakar", "Wong", "Tan", "Lee", "Samy", "Nair", "Abdullah", "Ismail", "Goh", "Kumar", "Mansor"]
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=180)

        with connection.cursor() as cursor:
            # 3. BERSIHKAN DATABASE
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            cursor.execute("TRUNCATE TABLE doctor_cusum_logs;")
            cursor.execute("TRUNCATE TABLE clinical_visits;")
            cursor.execute("TRUNCATE TABLE patients;")
            cursor.execute("TRUNCATE TABLE doctors;")
            cursor.execute("TRUNCATE TABLE clinics;")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

            total_doctors = 0
            total_visits = 0
            patient_counter = 1

            # 4. LOOP 150+ KLINIK
            for k in klinik_terpilih:
                cursor.execute("""
                    INSERT INTO clinics (name, state, lat, lon) 
                    VALUES (%s, %s, %s, %s)
                """, [k['name'], k['state'], k.get('lat', 0.0), k.get('lon', 0.0)])
                clinic_id = cursor.lastrowid

                # Jana 4 hingga 12 doktor setiap klinik
                num_doctors = random.randint(4, 12)
                for _ in range(num_doctors):
                    doc_name = f"Dr. {random.choice(first_names)} {random.choice(last_names)}"
                    doc_grade = random.choice(["UD41", "UD43", "UD47", "UD51", "UD54"])
                    
                    cursor.execute("INSERT INTO doctors (name, clinic_id, grade) VALUES (%s, %s, %s)", 
                                   [doc_name, clinic_id, doc_grade])
                    doctor_id = cursor.lastrowid
                    total_doctors += 1

                    # Jana 15-20 pesakit untuk setiap doktor
                    num_cases = random.randint(15, 20)
                    visits_for_doctor = []

                    for _ in range(num_cases):
                        patient_id = f"PID-{patient_counter:06d}"
                        gender = random.choice(['LELAKI', 'PEREMPUAN'])
                        age = random.randint(30, 75)
                        dm_type = random.choice(['TYPE 2', 'TYPE 2', 'TYPE 1'])
                        baseline_hba1c = round(random.uniform(7.0, 14.0), 1)
                        
                        cursor.execute("""
                            INSERT INTO patients (patient_id, gender, age, dm_type, baseline_hba1c)
                            VALUES (%s, %s, %s, %s, %s)
                        """, [patient_id, gender, age, dm_type, baseline_hba1c])
                        patient_counter += 1

                        visit_date = start_date + timedelta(days=random.randint(0, 180))
                        
                        # Realiti NCD: 60% berjaya, 40% gagal
                        is_success = random.random() < 0.6 
                        if is_success:
                            hba1c_reading = round(random.uniform(5.5, 7.0), 1)
                            hba1c_category = random.choice([1, 2])
                            x_value = 0 # BERJAYA
                        else:
                            hba1c_reading = round(random.uniform(7.1, 12.0), 1)
                            hba1c_category = random.choice([3, 4, 5])
                            x_value = 1 # GAGAL

                        visits_for_doctor.append({
                            'patient_id': patient_id, 'visit_date': visit_date,
                            'hba1c_reading': hba1c_reading, 'hba1c_category': hba1c_category, 'x_value': x_value
                        })

                    # 5. KIRA CUSUM BERNOULLI SECARA KRONOLOGI
                    visits_for_doctor.sort(key=lambda x: x['visit_date'])
                    m_value = 0.70
                    previous_score = 0.0

                    for sequence_k, visit in enumerate(visits_for_doctor, start=1):
                        cursor.execute("""
                            INSERT INTO clinical_visits (patient_id, doctor_id, visit_date, hba1c_reading, hba1c_category)
                            VALUES (%s, %s, %s, %s, %s)
                        """, [visit['patient_id'], doctor_id, visit['visit_date'].strftime('%Y-%m-%d'), visit['hba1c_reading'], visit['hba1c_category']])
                        visit_id = cursor.lastrowid

                        cusum_score = max(0, previous_score + visit['x_value'] - m_value)

                        cursor.execute("""
                            INSERT INTO doctor_cusum_logs (doctor_id, visit_id, sequence_k, x_value, m_value, previous_score, cusum_score)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, [doctor_id, visit_id, sequence_k, visit['x_value'], m_value, previous_score, cusum_score])
                        
                        previous_score = cusum_score
                        total_visits += 1

        self.stdout.write(self.style.SUCCESS(f"SELESAI! Data gergasi siap dijana:"))
        self.stdout.write(self.style.SUCCESS(f"- {len(klinik_terpilih)} Klinik (Semua Negeri Terlibat)"))
        self.stdout.write(self.style.SUCCESS(f"- {total_doctors} Doktor & {patient_counter - 1} Pesakit"))
        self.stdout.write(self.style.SUCCESS(f"- {total_visits} rekod CUSUM untuk 6 Bulan!"))