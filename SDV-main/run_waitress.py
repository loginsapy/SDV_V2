from waitress import serve
from vacations import create_app
import socket

app = create_app()

def get_local_ip():
    try:
        # Conecta a un DNS público para obtener la IP de la interfaz de red principal
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    ip = get_local_ip()
    port = 5001
    print(f"\n--- SERVIDOR ONLINE ---")
    print(f"Link para compartir con tus compañeros: http://{ip}:{port}")
    print(f"NOTA: Si no pueden entrar, asegúrate de permitir el puerto {port} en el Firewall de Windows.")
    print(f"-----------------------\n")
    serve(app, host="0.0.0.0", port=port)