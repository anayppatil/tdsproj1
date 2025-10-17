import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai
from github import Github, Auth
import time
import requests

load_dotenv()

app = Flask(__name__)

MY_APP_SECRET = os.environ.get('APP_SECRET')
GITHUB_PAT = os.environ.get('GITHUB_PAT')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

genai.configure(api_key=GEMINI_API_KEY)

def generate_app_code(brief, checks, attachments):
    """Generates application code using the Gemini API for a new app."""
    model = genai.GenerativeModel('gemini-2.5-pro')

    attachments_prompt_part = ""
    if attachments:
        attachment_details = []
        for att in attachments:
            attachment_details.append(f"- {att['name']}: provided as a data URI '{att['url']}'")
        attachments_str = "\n".join(attachment_details)
        attachments_prompt_part = f"""
The application must use the following data attachments:
{attachments_str}
Your JavaScript code must be able to fetch and process these data URIs.
"""

    prompt = f"""
    You are an expert front-end web developer.
    Your task is to create a single, self-contained 'index.html' file.
    This file must include all necessary HTML, inline CSS, and inline JavaScript.

    Here is the application brief: "{brief}"
    {attachments_prompt_part}
    The generated application will be tested against these checks: {checks}
    Ensure your code passes all of them. Return only the complete HTML code.
    """

    print("Generating code with Gemini...")
    response = model.generate_content(prompt)
    generated_code = response.text.strip().replace("```html", "").replace("```", "")
    print("Code generation complete.")
    return generated_code


def create_and_deploy_repo(task_id, generated_html):
    """Creates a GitHub repo, pushes code, and enables Pages."""
    try:
        auth = Auth.Token(GITHUB_PAT)
        g = Github(auth=auth)
        user = g.get_user()
        repo_name = f"llm-app-{task_id}"
        print(f"Creating repository: {repo_name}")
        repo = user.create_repo(repo_name, private=False)
        license_content = '''MIT License

            Copyright (c) 2025 Anay Patil

            Permission is hereby granted, free of charge, to any person obtaining a copy
            of this software and associated documentation files (the "Software"), to deal
            in the Software without restriction, including without limitation the rights
            to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
            copies of the Software, and to permit persons to whom the Software is
            furnished to do so, subject to the following conditions:

            The above copyright notice and this permission notice shall be included in all
            copies or substantial portions of the Software.

            THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
            IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
            FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
            AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
            LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
            OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
            SOFTWARE.
        '''
        repo.create_file("LICENSE", "feat: Add LICENSE", license_content, branch="main")
        readme_content = f"# {repo_name}\n\nThis project was auto-generated for the TDS Project."
        repo.create_file("README.md", "feat: Add README", readme_content, branch="main")
        repo.create_file("index.html", "feat: Add application code", generated_html, branch="main")
        print("Enabling GitHub Pages via API...")
        headers = {
            "Authorization": f"token {GITHUB_PAT}",
            "Accept": "application/vnd.github.v3+json"
        }
        payload = {"source": {"branch": "main", "path": "/"}}
        pages_endpoint = f"https://api.github.com/repos/{user.login}/{repo.name}/pages"
        response = requests.post(pages_endpoint, headers=headers, json=payload)

        if response.status_code != 201:
            print(f"Failed to enable GitHub Pages. Status: {response.status_code}, Response: {response.text}")
            return None

        pages_url = response.json().get("html_url")
        print(f"GitHub Pages enabled. URL will be: {pages_url}")
        
        deployment_successful = wait_for_github_pages_deployment(repo)
        if not deployment_successful:
            print("Aborting due to deployment failure or timeout.")
            return None

        commit_sha = repo.get_branch("main").commit.sha

        return {"repo_url": repo.html_url, "pages_url": pages_url, "commit_sha": commit_sha}
    except Exception as e:
        print(f"An error occurred during GitHub operations: {e}")
        return None

def wait_for_github_pages_deployment(repo):
    """Polls the GitHub API to check for a successful Pages deployment."""
    print("Waiting for GitHub Pages deployment to complete...")
    time.sleep(5) 
    
    timeout_seconds = 300 
    poll_interval_seconds = 10
    elapsed_time = 0

    while elapsed_time < timeout_seconds:
        try:
            workflow_runs = repo.get_workflow_runs()
            if workflow_runs.totalCount > 0:
                latest_run = workflow_runs[0]
                
                print(f"  - Run status: {latest_run.status}, Conclusion: {latest_run.conclusion}")
                if latest_run.status == "completed":
                    if latest_run.conclusion == "success":
                        print("✅ Deployment successful!")
                        return True
                    else:
                        print(f"❌ Deployment failed with conclusion: {latest_run.conclusion}")
                        return False
            
            time.sleep(poll_interval_seconds)
            elapsed_time += poll_interval_seconds
            
        except Exception as e:
            print(f"An error occurred while checking workflow status: {e}")
            return False

    print("❌ Deployment check timed out after 2 minutes.")
    return False

def process_build_request(data):
    """Orchestrates the build and deployment process for Round 1."""
    print("Build task started!")
    generated_code = generate_app_code(data.get('brief'), data.get('checks'), data.get('attachments', []))
    if not generated_code:
        return

    github_details = create_and_deploy_repo(data.get('task'), generated_code)
    if not github_details:
        return
    notification_payload = {
        "email": data.get('email'),
        "task": data.get('task'),
        "round": 1,
        "nonce": data.get('nonce'),
        "repo_url": github_details['repo_url'],
        "commit_sha": github_details['commit_sha'],
        "pages_url": github_details['pages_url']
    }
    notify_evaluator(data.get('evaluation_url'), notification_payload)
    print("Build task finished.")


# --- Round 2: Revise Logic ---

def get_existing_repo_details(task_id):
    """Finds a repo and fetches the content of index.html and README.md."""
    try:
        auth = Auth.Token(GITHUB_PAT)
        g = Github(auth=auth)
        user = g.get_user()
        repo_name = f"llm-app-{task_id}"
        repo = user.get_repo(repo_name)
        index_file = repo.get_contents("index.html")
        readme_file = repo.get_contents("README.md")
        
        existing_code = {
            "index.html": index_file.decoded_content.decode("utf-8"),
            "README.md": readme_file.decoded_content.decode("utf-8")
        }
        
        return {"repo": repo, "user": user, "existing_code": existing_code}
    except Exception as e:
        print(f"Error fetching repo files for {repo_name}: {e}")
        return None


def generate_updated_code(brief, checks, existing_code, attachments):
    """Generates updated code for both index.html and README.md."""
    model = genai.GenerativeModel('gemini-2.5-pro')

    attachments_prompt_part = ""
    if attachments:
        attachment_details = "\n".join([f"- {att['name']}" for att in attachments])
        attachments_prompt_part = f"""
        The project uses data from these attachments: {attachment_details}
        The content for these is embedded or fetched in the existing code. Ensure the updated code continues to use this data correctly.
        """

    prompt = f"""
    You are an expert software developer modifying a project.
    Here are the current files:

    --- FILE: index.html ---
    {existing_code['index.html']}

    --- FILE: README.md ---
    {existing_code['README.md']}

    --- END OF FILES ---

    {attachments_prompt_part}

    Now, update the files to implement the following request: "{brief}"
    The updated code must pass these checks: {checks}

    Your response must contain the complete, new versions of BOTH files.
    Structure your response exactly like this, with no other text or explanations:

    --- FILE: index.html ---
    (new html code here)

    --- FILE: README.md ---
    (new readme content here)
    """
    
    print("Generating updated code for all files with Gemini...")
    response = model.generate_content(prompt)
    print("Code update generation complete.")
    return response.text

def update_repo_files(repo, llm_response):
    """Parses the LLM response, updates files, and waits for redeployment."""
    try:
        print("Parsing LLM response and updating repository files...")
        files_raw = llm_response.split("--- FILE: ")
        
        new_commit_sha = ""
        for file_raw in files_raw:
            if not file_raw.strip():
                continue
            
            print("Processing file block...")
            
            parts = file_raw.split("\n", 1)
            if len(parts) < 2:
                print("Warning: Skipping malformed file block.")
                continue
            
            filename = parts[0].split("---")[0].strip()
            content = parts[1].strip().replace("```html", "").replace("```", "")

            if filename in ["index.html", "README.md"]:
                print(f"Found valid file: {filename}")
                original_file = repo.get_contents(filename)
                
                result = repo.update_file(
                    path=original_file.path,
                    message=f"feat: Update {filename} based on revision",
                    content=content,
                    sha=original_file.sha
                )
                new_commit_sha = result['commit'].sha
                print(f"Successfully updated {filename}. New commit: {new_commit_sha}")
            else:
                print(f"Warning: Skipping unexpected file found in LLM response: {filename}")

        if not new_commit_sha:
            print("Error: No valid files were updated.")
            return None
            
        deployment_successful = wait_for_github_pages_deployment(repo)
        if not deployment_successful:
            print("Aborting due to redeployment failure or timeout.")
            return None

        return new_commit_sha
    except Exception as e:
        print(f"Failed to update repository files with exception: {e}")
        return None

def process_revise_request(data):
    """Orchestrates the revision of an existing application for Round 2."""
    print("Revision task started!")
    task_id, brief, checks = data.get('task'), data.get('brief'), data.get('checks')
    attachments = data.get('attachments', [])
    
    repo_details = get_existing_repo_details(task_id)
    if not repo_details: return
    llm_response = generate_updated_code(brief, checks, repo_details['existing_code'], attachments)
    if not llm_response: return
    commit_sha = update_repo_files(repo_details['repo'], llm_response)
    if not commit_sha: return
    
    notification_payload = {
        "email": data.get('email'), "task": task_id, "round": 2,
        "nonce": data.get('nonce'), "repo_url": repo_details['repo'].html_url,
        "commit_sha": commit_sha, "pages_url": f"https://{repo_details['user'].login}.github.io/{repo_details['repo'].name}/"
    }
    notify_evaluator(data.get('evaluation_url'), notification_payload)
    print("Revision task finished.")

def notify_evaluator(evaluation_url, payload):
    """Sends a POST request to the evaluation URL with retry logic."""
    max_retries, delay = 5, 1
    for i in range(max_retries):
        try:
            print(f"Sending notification to {evaluation_url}...")
            response = requests.post(evaluation_url, json=payload, timeout=10)
            if response.status_code == 200:
                print("Notification successful.")
                return True
            print(f"Attempt {i + 1} failed with status {response.status_code}.")
        except requests.exceptions.RequestException as e:
            print(f"Attempt {i + 1} failed with an exception: {e}")
        
        time.sleep(delay)
        delay *= 2
    print("Notification failed after all retries.")
    return False

@app.route('/api/project', methods=['POST'])
def handle_project_request():
    """Main endpoint to handle both build and revise requests."""
    data = request.json
    if not data or data.get('secret') != MY_APP_SECRET:
        return jsonify({"error": "Invalid or missing secret"}), 403

    if data.get('round') == 2:
        process_revise_request(data)
    elif data.get('round') == 1:
        process_build_request(data)

    return jsonify({"status": "Request processed successfully."}), 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)