from flask import Flask, request, jsonify
from tinydb import TinyDB, Query
import hashlib
import time

# O banco de dados será um arquivo JSON chamado 'licenses.json'
db = TinyDB('licenses.json') 

app = Flask(__name__)

# Rota para ativar uma licença
@app.route('/api/v1/activate', methods=['POST'])
def activate_license():
    data = request.get_json()
    license_key = data.get('license_key')
    device_id = data.get('device_id')

    if not license_key or not device_id:
        return jsonify({"success": False, "message": "Dados incompletos."}), 400

    License = Query()
    license_record = db.get(License.key == license_key)

    if not license_record:
        return jsonify({"success": False, "message": "Chave de licença inválida."}), 401

    if license_record['status'] == 'active' and license_record.get('device_id') != device_id:
        # A chave já está ativa em outro dispositivo
        return jsonify({"success": False, "message": "Esta chave já está em uso em outro computador."}), 403

    if license_record['status'] == 'inactive':
        # Ativa a chave e associa ao novo dispositivo
        db.update({'status': 'active', 'device_id': device_id, 'activated_at': time.time()}, License.key == license_key)
        return jsonify({"success": True, "message": "Licença ativada com sucesso."}), 200

    # A chave já está ativa e associada a este dispositivo
    return jsonify({"success": True, "message": "Licença já está ativa neste computador."}), 200

# Rota de administrador para gerar chaves (IMPORTANTE: não a exponha publicamente!)
@app.route('/api/v1/generate_key', methods=['POST'])
def generate_key():
    new_key = hashlib.sha256(str(time.time()).encode()).hexdigest()
    db.insert({'key': new_key, 'status': 'inactive'})
    print(f"Nova chave gerada: {new_key}") 
    return jsonify({"success": True, "key": new_key}), 200

# Para testes, rode em debug mode. Para produção, é recomendado usar um WSGI.
if __name__ == '__main__':
    # Quando você rodar 'py server.py' localmente, esta linha será executada.
    app.run(debug=True, port=5000)