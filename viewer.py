import tkinter as tk
from tkinter import ttk, messagebox
import requests
import urllib3
import socket

# Suppress warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_local_ip():
    """Get the local IP address used for outgoing connections."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a dummy address to determine the local IP
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

SERVER_IP = get_local_ip()
SERVER_URL = f"https://{SERVER_IP}:5000/api/report"

class FIMSViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("FIMS Viewer")
        self.root.geometry("1000x600")

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        
        # Header
        header_frame = tk.Frame(root, bg="#0f172a", height=60)
        header_frame.pack(fill="x")
        
        lbl_title = tk.Label(header_frame, text="File Integrity Monitoring Logs", 
                             font=("Segoe UI", 16, "bold"), bg="#0f172a", fg="white")
        lbl_title.pack(side="left", padx=20, pady=10)

        btn_refresh = tk.Button(header_frame, text="Refresh Logs", command=self.load_data,
                                bg="#38bdf8", fg="#0f172a", font=("Segoe UI", 10, "bold"),
                                relief="flat", padx=15)
        btn_refresh.pack(side="right", padx=20, pady=10)

        # Treeview (Table)
        columns = ("Time", "Agent", "Severity", "Event", "File")
        self.tree = ttk.Treeview(root, columns=columns, show="headings")
        
        self.tree.heading("Time", text="Timestamp")
        self.tree.heading("Agent", text="Agent Name")
        self.tree.heading("Severity", text="Severity")
        self.tree.heading("Event", text="Event Type")
        self.tree.heading("File", text="File Path")

        self.tree.column("Time", width=150)
        self.tree.column("Agent", width=100)
        self.tree.column("Severity", width=100)
        self.tree.column("Event", width=100)
        self.tree.column("File", width=400)

        # Scrollbar
        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        self.tree.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        scrollbar.pack(side="right", fill="y", pady=10)

        # Tags for coloring
        self.tree.tag_configure("CRITICAL", foreground="red")
        self.tree.tag_configure("WARNING", foreground="orange")
        self.tree.tag_configure("INFO", foreground="black")

        # Initial Load
        self.load_data()

    def load_data(self):
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            response = requests.get(SERVER_URL, verify=False)
            if response.status_code == 200:
                logs = response.json()
                for log in logs:
                    values = (log['timestamp'], log['agent_name'], log['severity'], 
                              log['event_type'], log['file_path'])
                    self.tree.insert("", "end", values=values, tags=(log['severity'],))
            else:
                messagebox.showerror("Error", f"Failed to fetch logs: {response.status_code}")
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Connection Error", "Could not connect to FIMS Server.\nEnsure 'server.py' is running.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

if __name__ == "__main__":
    root = tk.Tk()
    app = FIMSViewer(root)
    root.mainloop()
