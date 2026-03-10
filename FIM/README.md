# File Integrity Monitoring System (FIMS)

A distributed File Integrity Monitoring System built with Python, utilizing a central FastAPI server and multiple agents.

## Project Structure

- **`server.py`**: Central server (FastAPI) that stores logs and hosts the dashboard.
- **`fims_agent.py`**: Agent script that monitors files and sends logs to the server.
- **`viewer.py`**: GUI application (Tkinter) to view logs remotely.
- **`generate_cert.py`**: Script to generate self-signed SSL certificates.
- **`templates/dashboard.html`**: Web dashboard template.
- **`fims.db`**: SQLite database (created on first run).

## Prerequisites

- Python 3.8+
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

## Setup & Running

### 1. Generate SSL Certificates
The system uses HTTPS. Access to the dashboard and API requires SSL certificates.
```bash
python generate_cert.py
```
This will create `cert.pem` and `key.pem`.

### 2. Start the Server
Run the central server. It will listen on port 5000.
```bash
python server.py
# Output: Uvicorn running on https://0.0.0.0:5000
```
Open your browser and navigate to `https://localhost:5000` to see the dashboard.
*Note: Accept the security warning for the self-signed certificate.*

### 3. Start the Agent(s)
Run the agent on the machine(s) you want to monitor.
```bash
python fims_agent.py
```
The agent monitors `./test_monitor` and `./important_files` by default. You can modify `DIRECTORIES_TO_WATCH` in `fims_agent.py`.

### 4. Start the Viewer (Optional)
Run the GUI viewer for a desktop experience.
```bash
python viewer.py
```

## Demo Steps

1.  Start `server.py`.
2.  Start `fims_agent.py`.
3.  Open `https://localhost:5000` in a browser.
4.  Create a file in the `test_monitor` folder:
    - New logs should appear in the terminal and dashboard as `FILE_ADDED` (Warning).
5.  Modify the file:
    - Logs should appear as `FILE_MODIFIED` (Critical).
6.  Delete the file:
    - Logs should appear as `FILE_DELETED` (Warning).
7.  Use `viewer.py` to see the same logs in the desktop app.

## Architecture

- **Agent**: Calculates SHA-256 hashes of files. Polls every 5 seconds.
- **Communication**: HTTP POST with JSON payload. Authenticated via `x-api-key` header.
- **Storage**: SQLite database.
