import re   # REGEX
import glob
import sys
import os

def parse_msa_file(filepath):
    with open(filepath, 'r') as file:
        text = file.read()

    # 1. Extract the Subject (The core issue)
    subject_match = re.search(r"Subject:\s*(.*)", text)
    subject = subject_match.group(1) if subject_match else "Unknown Alert"

    # 2. Extract the Action Deadline
    date_match = re.search(r"Action advised before\s*(.*?):", text)
    deadline = date_match.group(1) if date_match else "Unknown Deadline"

    # 3. Extract the Project IDs
    # Look for everything between the intro line and the footer
    project_section = re.search(r"Your affected projects are listed below:\n(.*?)\n\nWE'RE HERE TO HELP", text, re.DOTALL)
    
    projects = []
    if project_section:
        # Split by newline and strip whitespace
        projects = [p.strip() for p in project_section.group(1).strip().split('\n') if p.strip()]

    return {
        "alert": subject,
        "deadline": deadline,
        "impacted_projects": projects
    }

# Loop through your files
# parsed_data = [parse_msa_file(f) for f in glob.glob("*.txt")]  

if __name__ == "__main__":
    # Running format would be "python3 msa_parser.py <filename> <filename> <filename> ..."
    # Loop through files and sort them in priority.
    # Write them out to another big file. 
    
    if (len(sys.argv)) < 2:
        print("Please run with at least one MSA file.")


