import subprocess
import sys
import time
import os

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def run_supervisor():
    print("🚀 Starting Arbitrage MVP Supervisor Launcher...")
    print("-" * 40)
    
    server_cmd = [sys.executable, "-m", "uvicorn", "dashboard.main:app", "--host", "127.0.0.1", "--port", "8000"]
    engine_cmd = [sys.executable, "-u", "run.py"]
    sec_cmd = [sys.executable, "-u", "-m", "engine.sec_logger"]
    
    server_proc = None
    engine_proc = None
    sec_proc = None
    
    try:
        while True:
            # 1. Maintain Dashboard Server
            if server_proc is None or server_proc.poll() is not None:
                if server_proc is not None:
                    print(f"\n⚠️  Dashboard server exited with code {server_proc.poll()}. Restarting in 2s...")
                    time.sleep(2)
                print("🟢 Starting Dashboard Server (http://127.0.0.1:8000)...")
                server_proc = subprocess.Popen(server_cmd)
                time.sleep(1)

            # 2. Maintain Scraping Engine
            if engine_proc is None or engine_proc.poll() is not None:
                if engine_proc is not None:
                    print(f"\n⚠️  Scraping engine exited with code {engine_proc.poll()}. Restarting in 3s...")
                    time.sleep(3)
                print("🟢 Starting Scraping Engine (run.py)...")
                engine_proc = subprocess.Popen(engine_cmd)

            # 3. Maintain High-Frequency 1s Tick Logger & Auto Paper Trader
            if sec_proc is None or sec_proc.poll() is not None:
                if sec_proc is not None:
                    print(f"\n⚠️  1s Tick Logger exited with code {sec_proc.poll()}. Restarting in 2s...")
                    time.sleep(2)
                print("🟢 Starting 1s HFT Tick Logger & Auto Paper Trader (engine.sec_logger)...")
                sec_proc = subprocess.Popen(sec_cmd)

            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\n🛑 Shutting down supervisor...")
        for p in [engine_proc, server_proc, sec_proc]:
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(1)
        print("✅ Shutdown complete.")

if __name__ == "__main__":
    run_supervisor()
