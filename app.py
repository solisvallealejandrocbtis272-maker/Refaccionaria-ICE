import os
from bson.objectid import ObjectId
from bson.errors import InvalidId
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify, session
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "clave_secreta_super_segura"  # Necesario para manejar sesiones de usuario

# Conexión a MongoDB (Local o Atlas)
mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb://127.0.0.1:27017/"

cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
base_datos = cliente["taller_automotriz"]
piezas = base_datos["piezas"]
usuarios = base_datos["usuarios"]  # Nueva colección para cuentas de usuario

# --- RUTAS DE AUTENTICACIÓN (Registro e Inicio de Sesión) ---

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre_usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("password", "").strip()
        
        if not nombre_usuario or not contrasena:
            return render_template("registro.html", error="Completa todos los campos.")
        
        # Verificar si el usuario ya existe
        if usuarios.find_one({"usuario": nombre_usuario}):
            return render_template("registro.html", error="El usuario ya existe.")
        
        # Encriptar contraseña y guardar
        hash_password = generate_password_hash(contrasena)
        usuarios.insert_one({"usuario": nombre_usuario, "password": hash_password})
        return redirect(url_for("login"))
        
    return render_template("registro.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nombre_usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("password", "").strip()
        
        user = usuarios.find_one({"usuario": nombre_usuario})
        if user and check_password_hash(user["password"], contrasena):
            session["usuario"] = user["usuario"]
            return redirect(url_for("inicio"))
        
        return render_template("login.html", error="Usuario o contraseña incorrectos.")
        
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("usuario", None)
    return redirect(url_for("login"))

# --- RUTAS DEL TALLER Y BUSCADOR ---

@app.route("/")
def inicio():
    if "usuario" not in session:
        return redirect(url_for("login"))
    
    # Capturar término de búsqueda si el usuario escribe algo en el buscador
    busqueda = request.args.get("q", "").strip()
    
    if busqueda:
        # Filtrar piezas por nombre o categoría usando comodines ($regex)
        lista = list(piezas.find({
            "$or": [
                {"nombre": {"$regex": busqueda, "$options": "i"}},
                {"categoria": {"$regex": busqueda, "$options": "i"}}
            ]
        }).sort("nombre", 1))
    else:
        lista = list(piezas.find().sort("nombre", 1))
        
    return render_template("index.html", piezas=lista, busqueda=busqueda)

@app.route("/catalogo")
def catalogo():
    # Página extra pública o exclusiva para visualizar el inventario en formato de tarjetas/catálogo
    busqueda = request.args.get("q", "").strip()
    if busqueda:
        lista = list(piezas.find({
            "$or": [
                {"nombre": {"$regex": busqueda, "$options": "i"}},
                {"categoria": {"$regex": busqueda, "$options": "i"}}
            ]
        }).sort("nombre", 1))
    else:
        lista = list(piezas.find().sort("nombre", 1))
        
    return render_template("catalogo.html", piezas=lista, busqueda=busqueda)

@app.route("/agregar", methods=["POST"])
def agregar():
    if "usuario" not in session:
        return redirect(url_for("login"))
        
    nombre = request.form.get("nombre", "").strip()
    categoria = request.form.get("categoria", "").strip()
    estado = request.form.get("estado", "").strip()
    
    if not nombre or not categoria or not estado:
        abort(400, description="Completa todos los campos de la refacción.")
        
    piezas.insert_one({"nombre": nombre, "categoria": categoria, "estado": estado})
    return redirect(url_for("inicio") + "#lista")

@app.route("/editar/<id>", methods=["GET", "POST"])
def editar(id):
    if "usuario" not in session:
        return redirect(url_for("login"))
        
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404, description="ID no válido.")
        
    pieza = piezas.find_one({"_id": oid})
    if not pieza:
        abort(404, description="La pieza no existe.")
        
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        categoria = request.form.get("categoria", "").strip()
        estado = request.form.get("estado", "").strip()
        
        piezas.update_one({"_id": oid}, {"$set": {"nombre": nombre, "categoria": categoria, "estado": estado}})
        return redirect(url_for("inicio") + "#lista")
        
    return render_template("editar.html", pieza=pieza)

@app.route("/eliminar/<id>", methods=["POST"])
def eliminar(id):
    if "usuario" not in session:
        return redirect(url_for("login"))
        
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404, description="ID no válido.")
        
    piezas.delete_one({"_id": oid})
    return redirect(url_for("inicio") + "#lista")

@app.route("/ayuda")
def ayuda():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    return render_template("ayuda.html", fecha=fecha)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)