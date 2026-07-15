import re   # REGEX
import glob
import sys
import os
import csv
from pathlib import Path

GCP_SERVICES = [ "apigee mcp", "apigee",
    "bigquery",
    "cloud storage",
    "compute engine",
    "cloud functions",
    "google kubernetes engine",
    "pub/sub",
    "vertex ai",
]

MSA_KEYWORDS_DIR = Path(__file__).parent / "msa_data" / "msa_keywords_cleaned"

def parse_msa_file(filepath):
    with open(filepath, 'r') as file:
        text = file.read()
    # Also add:
    # Date it's being sent 
    # what you need to do - with action deadline
    # affected services - servies the user is using 
    # 

    # Subject (The core issue)
    subject_match = re.search(r"Subject:\s*(.*)", text)
    subject = subject_match.group(1) if subject_match else "Unknown Alert"

    # Date it is being sent 
    date_match = re.search(r"Date:\s*(.*)", text)
    date = date_match.group(1) if date_match else "Unknown Date"

    # Action Deadline
    deadline_match = re.search(r"Action advised before\s*(.*?):", text)
    deadline = deadline_match.group(1) if deadline_match else "Unknown Deadline"

    #Affected Services
    header_region = text.split("WHAT YOU NEED TO KNOW")[0]
    service_matches = [svc for svc in GCP_SERVICES if svc.lower() in header_region.lower()]
    affected_services = max(service_matches, key=len) if service_matches else "Unknown Service"

    # Project IDs
    # Look for everything between the intro line and the footer
    project_section = re.search(r"Your affected projects are listed below:\n(.*?)\n\nWE'RE HERE TO HELP", text, re.DOTALL)
    
    projects = []
    if project_section:
        # Split by newline and strip whitespace
        projects = [p.strip() for p in project_section.group(1).strip().split('\n') if p.strip()]

    print(subject)
    print(date)
    print(deadline)
    print(projects)
    print(affected_services)
    print()

    # ADDED: write the keyword CSV that combine_and_send.py / msa_chatbot.py
    # actually read via read_keywords(). 
    if affected_services != "Unknown Service":
        msa_id = Path(filepath).stem
        MSA_KEYWORDS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = MSA_KEYWORDS_DIR / f"{msa_id}_keywords.csv"
        with out_path.open("w", newline="", encoding="utf-8") as out_file:
            writer = csv.writer(out_file)
            writer.writerow([affected_services.strip().casefold()])
        print(f"Wrote {out_path}")
        print()


    return {
        "alert": subject,
        "date": date,
        "deadline": deadline,
        "impacted_projects": projects,
        "affected_service": affected_services 
    }

# Loop through your files
# parsed_data = [parse_msa_file(f) for f in glob.glob("*.txt")]  

if __name__ == "__main__":
    # Running format would be "python3 msa_parser.py <filename> <filename> <filename> ..."
    # Loop through files and sort them in priority.
    # Write them out to another big file. 
    
    if (len(sys.argv)) < 2:
        print("Please run with at least one MSA file.")
        sys.exit()

    for i in range(1, len(sys.argv)):
        parse_msa_file(sys.argv[i])

