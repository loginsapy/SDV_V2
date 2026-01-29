import os
import socket
from vacations import create_app

# Se crea la instancia de la aplicación llamando a la fábrica
app = create_app()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "True") == "True"
    
    ip = get_local_ip()
    port = 5001
    print(f"\n--- MODO DESARROLLO ---")
    print(f"Link local: http://{ip}:{port}")
    print(f"-----------------------\n")
    
    # Se ejecuta la aplicación
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
