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

    # NOVO: Verifica se a licença foi revogada
    if license_record.get('revoked', False):
        return jsonify({"success": False, "message": "Esta licença foi revogada."}), 403

    if license_record['status'] == 'active' and license_record.get('device_id') != device_id:
        return jsonify({"success": False, "message": "Esta chave já está em uso em outro computador."}), 403

    if license_record['status'] == 'inactive':
        db.update({'status': 'active', 'device_id': device_id, 'activated_at': time.time()}, License.key == license_key)
        return jsonify({"success": True, "message": "Licença ativada com sucesso."}), 200

    return jsonify({"success": True, "message": "Licença já está ativa neste computador."}), 200

# NOVO: Rota para revogar uma licença manualmente
@app.route('/api/v1/revoke', methods=['POST'])
def revoke_license():
    data = request.get_json()
    license_key = data.get('license_key')

    if not license_key:
        return jsonify({"success": False, "message": "Chave de licença ausente."}), 400

    License = Query()
    license_record = db.get(License.key == license_key)

    if not license_record:
        return jsonify({"success": False, "message": "Chave de licença inválida."}), 401
    
    if license_record['status'] == 'active':
        db.update({'revoked': True}, License.key == license_key)
        return jsonify({"success": True, "message": "Licença revogada com sucesso."}), 200
    
    return jsonify({"success": False, "message": "A licença já está inativa ou revogada."}), 200

# Rota de administrador para gerar chaves
@app.route('/api/v1/generate_key', methods=['POST'])
def generate_key():
    new_key = hashlib.sha256(str(time.time()).encode()).hexdigest()
    # Adicione a flag 'revoked' como False por padrão
    db.insert({'key': new_key, 'status': 'inactive', 'revoked': False})
    print(f"Nova chave gerada: {new_key}") 
    return jsonify({"success": True, "key": new_key}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)