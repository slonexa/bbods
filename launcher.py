import subprocess
import sys
import time
import threading

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def watch_process(name, proc, other_proc):
    """Watch a process and terminate the other if it dies."""
    proc.wait()
    code = proc.returncode
    if code != 0:
        print(f"\n⚠️  {name} exited with code {code}. Stopping everything...")
        try:
            other_proc.terminate()
        except Exception:
            pass

def main():
    print("🚀 Starting Arbitrage MVP Launcher...")
    print("-" * 40)
    
    # 1. Start the FastAPI dashboard server
    print("🟢 Starting Dashboard Server (FastAPI)...")
    print("   Dashboard: http://127.0.0.1:8000")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dashboard.main:app", "--host", "127.0.0.1", "--port", "8000"]
    )
    
    # Give the server a moment to spin up
    time.sleep(2)
    print("-" * 40)
    
    # 2. Start the scraping engine (data collection loop)
    print("🟢 Starting Scraping Engine...")
    engine_process = subprocess.Popen(
        [sys.executable, "run.py"]
    )
    
    # 3. Watch both processes — if either dies unexpectedly, kill the other
    t1 = threading.Thread(target=watch_process, args=("Dashboard", server_process, engine_process), daemon=True)
    t2 = threading.Thread(target=watch_process, args=("Engine", engine_process, server_process), daemon=True)
    t1.start()
    t2.start()
    
    try:
        # Block until either exits
        while server_process.poll() is None and engine_process.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n🛑 Shutting down...")
        for proc in [engine_process, server_process]:
            if proc.poll() is None:
                proc.terminate()
        for proc in [engine_process, server_process]:
            proc.wait()
        print("✅ Shutdown complete.")

if __name__ == "__main__":
    main()
