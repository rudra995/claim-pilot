"""The 25 procedures the synthetic hospital treats, with cost and stay ranges (design section 8).

`core` is the cost of everything except the room, ICU and implant, in rupees.
ICD-10 codes are from the WHO list. The text fields feed the documents written in phase 3.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Procedure:
    slug: str
    diagnosis: str
    icd10: str
    kind: Literal["surgical", "medical"]
    los: tuple[int, int]  # length of stay in days
    core: tuple[int, int]
    age: tuple[int, int]
    complaint: str
    reason: str  # why admission was needed, as a discharge summary would state it
    treatment: str
    lab_finding: str
    ortho: bool = False  # orthopaedic: a pre-op X-ray report is expected
    emergency: bool = False
    gender: Literal["M", "F"] | None = None
    implant: tuple[str, int, int] | None = None  # name, low, high
    icu_days: tuple[int, int] | None = None
    xray_finding: str | None = None


PROCEDURES: list[Procedure] = [
    Procedure(
        "laparoscopic_appendectomy", "Acute appendicitis", "K35.8", "surgical", (2, 4), (55_000, 95_000), (12, 65),
        "Right lower abdominal pain with fever and vomiting",
        "Acute appendicitis on ultrasound needing emergency surgery",
        "Laparoscopic appendectomy, IV antibiotics",
        "Raised white cell count; inflamed appendix on ultrasound",
        emergency=True,
    ),
    Procedure(
        "total_knee_replacement", "Primary osteoarthritis of knee", "M17.1", "surgical", (3, 6), (90_000, 140_000), (50, 80),
        "Knee pain for years, worse on walking, not relieved by physiotherapy",
        "End-stage knee osteoarthritis after failed conservative treatment, needing joint replacement",
        "Total knee replacement",
        "Pre-operative blood work within normal limits",
        ortho=True, implant=("Knee implant", 80_000, 120_000),
        xray_finding="Severe joint space loss with osteophytes, Kellgren-Lawrence grade 4",
    ),
    Procedure(
        "total_hip_replacement", "Primary osteoarthritis of hip", "M16.1", "surgical", (4, 7), (100_000, 150_000), (50, 85),
        "Hip pain and stiffness limiting walking",
        "Advanced hip osteoarthritis with loss of mobility, needing joint replacement",
        "Total hip replacement",
        "Pre-operative blood work within normal limits",
        ortho=True, implant=("Hip implant", 90_000, 140_000),
        xray_finding="Marked narrowing of the hip joint with subchondral sclerosis",
    ),
    Procedure(
        "cataract_surgery", "Senile cataract", "H25.9", "surgical", (1, 1), (12_000, 18_000), (50, 85),
        "Gradual blurring of vision in one eye",
        "Mature cataract impairing daily vision, needing lens replacement",
        "Phacoemulsification with intraocular lens",
        "Blood sugar and blood pressure controlled for surgery",
        implant=("Intraocular lens", 8_000, 13_000),
    ),
    Procedure(
        "coronary_angioplasty", "Coronary artery disease", "I25.1", "surgical", (2, 4), (90_000, 150_000), (40, 80),
        "Chest pain on exertion",
        "Significant coronary blockage on angiography needing stenting",
        "Coronary angioplasty with stent",
        "Raised troponin; lipid profile abnormal",
        emergency=True, implant=("Drug-eluting stent", 35_000, 70_000), icu_days=(1, 2),
    ),
    Procedure(
        "caesarean_section", "Delivery by caesarean section", "O82.9", "surgical", (3, 5), (50_000, 90_000), (20, 40),
        "Term pregnancy in labour",
        "Obstructed labour needing caesarean delivery",
        "Lower segment caesarean section",
        "Haemoglobin and blood group checked before surgery",
        gender="F",
    ),
    Procedure(
        "normal_delivery", "Spontaneous delivery", "O80.9", "medical", (2, 3), (25_000, 45_000), (20, 38),
        "Term pregnancy in labour",
        "Active labour at term needing hospital delivery",
        "Normal vaginal delivery",
        "Haemoglobin and blood group normal",
        gender="F",
    ),
    Procedure(
        "hernia_repair", "Inguinal hernia", "K40.9", "surgical", (1, 2), (25_000, 36_000), (20, 75),
        "Groin swelling with pain on lifting",
        "Symptomatic inguinal hernia at risk of obstruction, needing repair",
        "Laparoscopic hernia repair with mesh",
        "Pre-operative blood work within normal limits",
        implant=("Hernia mesh", 5_000, 9_000),
    ),
    Procedure(
        "laparoscopic_cholecystectomy", "Gallstones", "K80.2", "surgical", (2, 4), (60_000, 100_000), (25, 70),
        "Recurrent upper abdominal pain after meals",
        "Symptomatic gallstones with repeated attacks, needing gallbladder removal",
        "Laparoscopic cholecystectomy",
        "Liver function mildly raised; gallstones on ultrasound",
    ),
    Procedure(
        "hysterectomy", "Uterine fibroids", "D25.9", "surgical", (3, 5), (70_000, 120_000), (35, 60),
        "Heavy menstrual bleeding with anaemia",
        "Large fibroids causing anaemia not controlled by medicines, needing surgery",
        "Total abdominal hysterectomy",
        "Low haemoglobin; enlarged uterus on ultrasound",
        gender="F",
    ),
    Procedure(
        "tonsillectomy", "Chronic tonsillitis", "J35.0", "surgical", (1, 2), (14_000, 20_000), (8, 40),
        "Repeated sore throat infections",
        "Recurrent tonsillitis despite antibiotics, needing tonsil removal",
        "Tonsillectomy",
        "Blood counts and clotting normal",
    ),
    Procedure(
        "lithotripsy", "Kidney stone", "N20.0", "surgical", (1, 2), (25_000, 35_000), (25, 70),
        "Severe flank pain with blood in urine",
        "Kidney stone blocking urine flow, needing stone removal",
        "Ureteroscopic lithotripsy",
        "Blood in urine; stone seen on CT scan",
    ),
    Procedure(
        "turp", "Enlarged prostate", "N40", "surgical", (2, 4), (55_000, 90_000), (50, 85),
        "Difficulty passing urine",
        "Prostate enlargement with urine retention, needing surgery",
        "Transurethral resection of the prostate",
        "Raised post-void residual urine; PSA normal",
        gender="M",
    ),
    Procedure(
        "dengue_management", "Dengue fever", "A90", "medical", (3, 6), (20_000, 45_000), (10, 70),
        "High fever with body ache and rash",
        "Dengue with falling platelets needing monitoring and IV fluids",
        "IV fluids, platelet monitoring",
        "Dengue NS1 positive; platelets 62,000",
        emergency=True,
    ),
    Procedure(
        "pneumonia_management", "Pneumonia", "J18.9", "medical", (4, 8), (35_000, 70_000), (20, 85),
        "Fever, cough and breathlessness",
        "Pneumonia with low oxygen levels needing IV antibiotics and oxygen",
        "IV antibiotics, oxygen therapy",
        "Raised white cell count; oxygen saturation 89%",
        emergency=True,
    ),
    Procedure(
        "gastroenteritis_management", "Acute gastroenteritis", "A09", "medical", (2, 4), (15_000, 30_000), (10, 85),
        "Loose stools and vomiting with dizziness",
        "Gastroenteritis with dehydration and low blood pressure needing IV fluids",
        "IV fluids, antiemetics",
        "Low sodium and potassium; raised creatinine",
        emergency=True,
    ),
    Procedure(
        "typhoid_management", "Typhoid fever", "A01.0", "medical", (4, 7), (20_000, 40_000), (10, 65),
        "Fever for a week with abdominal pain",
        "Typhoid fever not responding to oral antibiotics, needing IV treatment",
        "IV antibiotics",
        "Blood culture positive for Salmonella Typhi",
        emergency=True,
    ),
    Procedure(
        "acute_mi_management", "Acute myocardial infarction", "I21.9", "medical", (4, 7), (60_000, 110_000), (40, 85),
        "Sudden severe chest pain with sweating",
        "Heart attack needing intensive cardiac monitoring",
        "Thrombolysis, cardiac monitoring",
        "Troponin markedly raised; ST elevation on ECG",
        emergency=True, icu_days=(2, 3),
    ),
    Procedure(
        "stroke_management", "Ischaemic stroke", "I63.9", "medical", (5, 10), (70_000, 130_000), (45, 85),
        "Sudden weakness of one side of the body",
        "Acute stroke needing intensive neurological monitoring",
        "Neurological monitoring, antiplatelets, physiotherapy",
        "Infarct on CT brain",
        emergency=True, icu_days=(2, 4),
    ),
    Procedure(
        "femur_fracture_fixation", "Fracture of neck of femur", "S72.0", "surgical", (4, 7), (70_000, 110_000), (40, 85),
        "Fall at home with hip pain, unable to stand",
        "Displaced femur neck fracture needing surgical fixation",
        "Internal fixation of femur fracture",
        "Pre-operative blood work within normal limits",
        ortho=True, emergency=True, implant=("Hip screw and plate", 30_000, 60_000),
        xray_finding="Displaced fracture of the femoral neck",
    ),
    Procedure(
        "acl_reconstruction", "Cruciate ligament tear", "S83.5", "surgical", (2, 3), (60_000, 90_000), (18, 45),
        "Knee giving way after a sports injury",
        "Complete ligament tear with knee instability, needing reconstruction",
        "Arthroscopic ACL reconstruction",
        "Pre-operative blood work within normal limits",
        ortho=True, implant=("Graft fixation implants", 25_000, 45_000),
        xray_finding="No fracture; MRI shows complete ACL tear",
    ),
    Procedure(
        "spinal_fusion", "Lumbar disc disorder", "M51.1", "surgical", (4, 7), (120_000, 180_000), (35, 75),
        "Back pain radiating down the leg with numbness",
        "Disc disease with nerve compression not relieved by treatment, needing fusion",
        "Lumbar spinal fusion",
        "Pre-operative blood work within normal limits",
        ortho=True, implant=("Pedicle screws and rods", 80_000, 140_000),
        xray_finding="Disc space narrowing at L4-L5 with instability",
    ),
    Procedure(
        "cabg", "Coronary artery disease", "I25.1", "surgical", (6, 10), (220_000, 320_000), (45, 80),
        "Chest pain on minimal exertion",
        "Blockage of several coronary arteries needing bypass surgery",
        "Coronary artery bypass grafting",
        "Lipid profile abnormal; three-vessel disease on angiography",
        icu_days=(2, 4),
    ),
    Procedure(
        "aki_dialysis", "Acute kidney failure", "N17.9", "medical", (3, 6), (45_000, 80_000), (30, 80),
        "Reduced urine output with swelling",
        "Acute kidney failure with high potassium needing dialysis",
        "Haemodialysis, fluid management",
        "Creatinine 6.2; potassium 6.4",
        emergency=True,
    ),
    Procedure(
        "sinus_surgery", "Chronic sinusitis", "J32.9", "surgical", (1, 2), (35_000, 55_000), (18, 60),
        "Blocked nose and facial pain for months",
        "Chronic sinusitis not relieved by medicines, needing endoscopic surgery",
        "Functional endoscopic sinus surgery",
        "Blood counts normal; sinus disease on CT",
    ),
]

BY_SLUG: dict[str, Procedure] = {p.slug: p for p in PROCEDURES}
