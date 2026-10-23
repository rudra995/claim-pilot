"""Synthetic patients. Names are random pairings from fixed lists, not real people.

Fixed lists instead of Faker keep the output byte-identical across machines and library versions.
"""

import random

from backend.schemas import Patient
from data.generator.procedures import Procedure

FIRST_NAMES = {
    "M": ["Aarav", "Arjun", "Rohan", "Vikram", "Suresh", "Ramesh", "Anil", "Kiran", "Manoj", "Rahul",
          "Sanjay", "Deepak", "Imran", "Joseph", "Harish", "Naveen", "Prakash", "Gopal", "Farhan", "Ravi"],
    "F": ["Priya", "Ananya", "Lakshmi", "Sunita", "Meera", "Kavya", "Divya", "Pooja", "Neha", "Asha",
          "Fatima", "Anjali", "Rekha", "Shalini", "Geeta", "Mary", "Swati", "Nandini", "Bhavana", "Zara"],
}
LAST_NAMES = ["Sharma", "Iyer", "Rao", "Nair", "Reddy", "Patel", "Menon", "Das", "Kulkarni", "Singh",
              "Gupta", "Khan", "Pillai", "Joshi", "Mehta", "Banerjee", "Shetty", "Fernandes", "Naidu", "Verma"]
CITIES = ["Bengaluru", "Mysuru", "Mangaluru", "Chennai", "Hyderabad", "Pune", "Mumbai", "Kochi",
          "Delhi", "Kolkata", "Ahmedabad", "Jaipur"]


def make_patient(rng: random.Random, patient_id: str, procedure: Procedure) -> Patient:
    gender = procedure.gender or rng.choice(["M", "F"])
    return Patient(
        patient_id=patient_id,
        name=f"{rng.choice(FIRST_NAMES[gender])} {rng.choice(LAST_NAMES)}",
        age=rng.randint(*procedure.age),
        gender=gender,
        city=rng.choice(CITIES),
    )
