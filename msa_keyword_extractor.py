import re   # REGEX
import glob
import sys
import os

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
    print()

    return {
        "alert": subject,
        "date": date,
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
        sys.exit()

    for i in range(1, len(sys.argv)):
        parse_msa_file(sys.argv[i])

