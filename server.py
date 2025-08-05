from flask import Flask, request, jsonify
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float
from sqlalchemy.orm import sessionmaker, declarative_base
from os import environ
import hashlib
import time

# O objeto 'app' precisa estar no escopo global para o Gunicorn
app = Flask(__name__)

# --- CONFIGURAÇÃO DO BANCO DE DADOS POSTGRESQL ---
# O Render injeta a DATABASE_URL como uma variável de ambiente
DATABASE_URL = environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurada. Certifique-se de que o banco de dados está conectado ao seu serviço no Render.")

# Configuração do SQLAlchemy
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

# Definição da tabela de licenças
class License(Base):
    __tablename__ = 'licenses'
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default='inactive')
    device_id = Column(String)
    activated_at = Column(Float)
    revoked = Column(Boolean, nullable=False, default=False)

# Cria a tabela no banco de dados, se ela ainda não existir
Base.metadata.create_all(engine)

# --- ROTAS DA API ---

# Rota para ativar uma licença
@app.route('/api/v1/activate', methods=['POST'])
def activate_license():
    data = request.get_json()
    license_key = data.get('license_key')
    device_id = data.get('device_id')

    if not license_key or not device_id:
        return jsonify({"success": False, "message": "Dados incompletos."}), 400

    session = Session()
    try:
        license_record = session.query(License).filter_by(key=license_key).first()

        if not license_record:
            return jsonify({"success": False, "message": "Chave de licença inválida."}), 401

        if license_record.revoked:
            return jsonify({"success": False, "message": "Esta licença foi revogada."}), 403

        if license_record.status == 'active' and license_record.device_id != device_id:
            return jsonify({"success": False, "message": "Esta chave já está em uso em outro computador."}), 403

        if license_record.status == 'inactive':
            license_record.status = 'active'
            license_record.device_id = device_id
            license_record.activated_at = time.time()
            session.commit()
            return jsonify({"success": True, "message": "Licença ativada com sucesso."}), 200

        return jsonify({"success": True, "message": "Licença já está ativa neste computador."}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"Erro interno no servidor: {e}"}), 500
    finally:
        session.close()

# Rota para revogar uma licença manualmente
@app.route('/api/v1/revoke', methods=['POST'])
def revoke_license():
    data = request.get_json()
    license_key = data.get('license_key')

    if not license_key:
        return jsonify({"success": False, "message": "Chave de licença ausente."}), 400

    session = Session()
    try:
        license_record = session.query(License).filter_by(key=license_key).first()

        if not license_record:
            return jsonify({"success": False, "message": "Chave de licença inválida."}), 401
        
        if not license_record.revoked:
            license_record.revoked = True
            session.commit()
            return jsonify({"success": True, "message": "Licença revogada com sucesso."}), 200
        
        return jsonify({"success": False, "message": "A licença já está revogada."}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"Erro interno no servidor: {e}"}), 500
    finally:
        session.close()

# NOVO: Rota para deletar uma licença
@app.route('/api/v1/licenses/<license_key>', methods=['DELETE'])
def delete_license(license_key):
    session = Session()
    try:
        license_record = session.query(License).filter_by(key=license_key).first()
        if not license_record:
            return jsonify({"success": False, "message": "Chave de licença não encontrada."}), 404
        
        session.delete(license_record)
        session.commit()
        return jsonify({"success": True, "message": "Licença deletada com sucesso."}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"Erro interno no servidor: {e}"}), 500
    finally:
        session.close()

# Rota de administrador para gerar chaves
@app.route('/api/v1/generate_key', methods=['POST'])
def generate_key():
    new_key = hashlib.sha256(str(time.time()).encode()).hexdigest()
    
    session = Session()
    try:
        new_license = License(key=new_key)
        session.add(new_license)
        session.commit()
        return jsonify({"success": True, "key": new_key}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"Erro interno no servidor: {e}"}), 500
    finally:
        session.close()

# Rota para visualizar todas as licenças
@app.route('/api/v1/licenses', methods=['GET'])
def get_all_licenses():
    session = Session()
    try:
        licenses = session.query(License).all()
        licenses_list = [{
            'key': lic.key,
            'status': lic.status,
            'device_id': lic.device_id,
            'activated_at': lic.activated_at,
            'revoked': lic.revoked
        } for lic in licenses]
        return jsonify(licenses_list), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro interno no servidor: {e}"}), 500
    finally:
        session.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)