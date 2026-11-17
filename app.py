import os
import uuid
from bson.objectid import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify, session
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "clave_secreta_super_segura_refaccionaria_ice"

mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb+srv://gekkos:22042506102008@buffalos.ybktt6y.mongodb.net/"

# Configuración con tz_aware=True como se solicita en la guía
cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000, tz_aware=True)
base_datos = cliente["refaccionaria_ice"]
piezas = base_datos["piezas"]
usuarios = base_datos["usuarios"]
pedidos = base_datos["pedidos"]
reservas = base_datos["reservas"]

DURACION_RESERVA_MINUTOS = 5

# Crear índices para reservas
try:
    reservas.create_index([("reserva_id", 1), ("producto_id", 1)], unique=True)
    reservas.create_index("vence_en")
except Exception:
    pass

def es_admin():
    return session.get("rol") == "admin"

def ahora_utc():
    return datetime.now(timezone.utc)

def obtener_reserva_id():
    if not session.get("reserva_id"):
        session["reserva_id"] = uuid.uuid4().hex
    return session["reserva_id"]

def liberar_reservas_vencidas():
    """Libera automáticamente los productos cuya reserva haya expirado."""
    vencidas = list(reservas.find({
        "estado": "activa",
        "vence_en": {"$lte": ahora_utc()}
    }))
    for candidata in vencidas:
        with cliente.start_session() as mongo_session:
            with mongo_session.start_transaction():
                eliminada = reservas.find_one_and_delete(
                    {"_id": candidata["_id"], "estado": "activa"},
                    session=mongo_session
                )
                if eliminada:
                    piezas.update_one(
                        {"_id": eliminada["producto_id"]},
                        {"$inc": {"reservadas": -eliminada["cantidad"]}},
                        session=mongo_session
                    )

def reservar_unidad(producto_id, reserva_id):
    ahora = ahora_utc()
    vence = ahora + timedelta(minutes=DURACION_RESERVA_MINUTOS)
    try:
        oid = ObjectId(producto_id)
    except InvalidId:
        return False

    # Asegurar que el producto tenga existencias inicializadas si no existen
    prod_actual = piezas.find_one({"_id": oid})
    if not prod_actual:
        return False
    
    if "existencias" not in prod_actual:
        piezas.update_one({"_id": oid}, {"$set": {"existencias": 10, "reservadas": 0}})

    # Actualización atómica segura compatible con MongoDB local y Atlas
    producto = piezas.find_one_and_update(
        {
            "_id": oid,
            "$expr": {
                "$gt": [
                    {"$subtract": [
                        {"$ifNull": ["$existencias", 10]},
                        {"$ifNull": ["$reservadas", 0]}
                    ]},
                    0
                ]
            }
        },
        {"$inc": {"reservadas": 1}},
        return_document=ReturnDocument.AFTER
    )
    
    if producto is None:
        return False

    reservas.update_one(
        {"reserva_id": reserva_id, "producto_id": oid},
        {
            "$inc": {"cantidad": 1},
            "$set": {"estado": "activa", "vence_en": vence},
            "$setOnInsert": {"creada_en": ahora}
        },
        upsert=True
    )
    return True

@app.context_processor
def datos_carrito():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    if not reserva_id:
        return {"cantidad_carrito": 0}
    items = reservas.find({
        "reserva_id": reserva_id,
        "estado": "activa",
        "vence_en": {"$gt": ahora_utc()}
    })
    cantidad = sum(int(item["cantidad"]) for item in items)
    return {"cantidad_carrito": cantidad}

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre_usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("password", "").strip()
        rol = "admin" if usuarios.count_documents({}) == 0 or request.form.get("es_admin") else "cliente"
        
        if not nombre_usuario or not contrasena:
            return render_template("registro.html", error="Completa todos los campos.")
        
        if usuarios.find_one({"usuario": nombre_usuario}):
            return render_template("registro.html", error="El nombre de usuario ya existe.")
        
        hash_password = generate_password_hash(contrasena)
        usuarios.insert_one({"usuario": nombre_usuario, "password": hash_password, "rol": rol})
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
            session["rol"] = user.get("rol", "cliente")
            return redirect(url_for("catalogo"))
        
        return render_template("login.html", error="Usuario o contraseña incorrectos.")
        
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("catalogo"))

@app.route("/")
@app.route("/catalogo")
def catalogo():
    liberar_reservas_vencidas()
    busqueda = request.args.get("q", "").strip()
    categoria_filtro = request.args.get("categoria", "").strip()
    orden = request.args.get("orden", "nombre_asc")

    query = {}
    if busqueda:
        query["$or"] = [
            {"nombre": {"$regex": busqueda, "$options": "i"}},
            {"marca": {"$regex": busqueda, "$options": "i"}},
            {"modelo": {"$regex": busqueda, "$options": "i"}},
            {"categoria": {"$regex": busqueda, "$options": "i"}}
        ]
    if categoria_filtro:
        query["categoria"] = categoria_filtro

    sort_option = [("nombre", 1)]
    if orden == "precio_asc":
        sort_option = [("precio", 1)]
    elif orden == "precio_desc":
        sort_option = [("precio", -1)]

    lista_piezas = list(piezas.find(query).sort(sort_option))
    
    # Calcular disponibilidad para cada producto en el catálogo
    for producto in lista_piezas:
        existencias = int(producto.get("existencias", 10))
        reservadas = int(producto.get("reservadas", 0))
        producto["disponibles"] = max(0, existencias - reservadas)

    categorias_disponibles = piezas.distinct("categoria")

    return render_template(
        "catalogo.html", 
        piezas=lista_piezas, 
        busqueda=busqueda, 
        categoria_filtro=categoria_filtro,
        categorias=categorias_disponibles,
        orden=orden
    )

@app.route("/producto/<id>")
def detalle_producto(id):
    liberar_reservas_vencidas()
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)
    pieza = piezas.find_one({"_id": oid})
    if not pieza:
        abort(404)
    return render_template("detalle.html", pieza=pieza)

# --- CARRITO Y RESERVAS TEMPORALES ---
@app.route("/carrito")
def ver_carrito():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    productos_carrito = []
    total = 0

    items = list(reservas.find({
        "reserva_id": reserva_id,
        "estado": "activa",
        "vence_en": {"$gt": ahora_utc()}
    })) if reserva_id else []

    for item in items:
        producto = piezas.find_one({"_id": item["producto_id"]})
        if not producto:
            continue
        cantidad = int(item["cantidad"])
        precio = float(producto.get("precio", 0))
        subtotal = round(precio * cantidad, 2)
        productos_carrito.append({
            "id": str(producto["_id"]),
            "nombre": producto.get("nombre", "Producto"),
            "marca": producto.get("marca", "ICE"),
            "precio": precio,
            "cantidad": cantidad,
            "subtotal": subtotal,
            "imagen": producto.get("imagen", "")
        })
        total += subtotal

    vence_en = min((i["vence_en"] for i in items), default=None)

    return render_template(
        "carrito.html", 
        carrito=productos_carrito, 
        total=round(total, 2),
        vence_en=vence_en
    )

@app.route("/carrito/agregar/<producto_id>", methods=["POST"])
def agregar_al_carrito(producto_id):
    if not ObjectId.is_valid(producto_id):
        abort(404)
    liberar_reservas_vencidas()
    reserva_id = obtener_reserva_id()
    
    if reservar_unidad(producto_id, reserva_id):
        # Opcional: flash o notificación de éxito
        pass
    else:
        # Si no hay disponibilidad, se rechaza la reserva
        pass
        
    return redirect(request.referrer or url_for("catalogo"))

@app.route("/carrito/eliminar/<producto_id>", methods=["POST"])
def eliminar_del_carrito(producto_id):
    if not ObjectId.is_valid(producto_id):
        abort(404)
    reserva_id = session.get("reserva_id")
    try:
        oid = ObjectId(producto_id)
    except InvalidId:
        abort(404)

    if reserva_id:
        with cliente.start_session() as mongo_session:
            with mongo_session.start_transaction():
                eliminada = reservas.find_one_and_delete(
                    {
                        "reserva_id": reserva_id,
                        "producto_id": oid,
                        "estado": "activa"
                    },
                    session=mongo_session
                )
                if eliminada:
                    piezas.update_one(
                        {"_id": oid},
                        {"$inc": {"reservadas": -eliminada["cantidad"]}},
                        session=mongo_session
                    )

    return redirect(url_for("ver_carrito"))

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    items = list(reservas.find({"reserva_id": reserva_id, "estado": "activa", "vence_en": {"$gt": ahora_utc()}}))
    
    if not items:
        return redirect(url_for("catalogo"))
        
    carrito_items = []
    total = 0
    for item in items:
        prod = piezas.find_one({"_id": item["producto_id"]})
        if prod:
            sub = float(prod.get("precio", 0)) * int(item["cantidad"])
            carrito_items.append({
                "id": str(prod["_id"]),
                "nombre": prod["nombre"],
                "precio": float(prod.get("precio", 0)),
                "cantidad": int(item["cantidad"])
            })
            total += sub

    if request.method == "POST":
        pedido = {
            "usuario": session.get("usuario", "Invitado"),
            "items": carrito_items,
            "total": total,
            "metodo_pago": request.form.get("metodo_pago"),
            "metodo_envio": request.form.get("metodo_envio"),
            "direccion": request.form.get("direccion"),
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        pedidos.insert_one(pedido)
        
        # Confirmar compra: descontar existencias y limpiar reservas activas
        for item in items:
            with cliente.start_session() as mongo_session:
                with mongo_session.start_transaction():
                    piezas.update_one(
                        {"_id": item["producto_id"]},
                        {"$inc": {"existencias": -item["cantidad"], "reservadas": -item["cantidad"]}},
                        session=mongo_session
                    )
                    reservas.delete_one({"_id": item["_id"]}, session=mongo_session)

        session.pop("reserva_id", None)
        return render_template("confirmacion.html", pedido=pedido)

    return render_template("checkout.html", carrito=carrito_items, total=total)

# --- PANEL DE ADMINISTRADOR ---
@app.route("/admin/inventario")
def admin_inventario():
    if not es_admin():
        abort(403)
    liberar_reservas_vencidas()
    lista = list(piezas.find().sort("nombre", 1))
    return render_template("admin_inventario.html", piezas=lista)

@app.route("/admin/agregar", methods=["GET", "POST"])
def admin_agregar():
    if not es_admin():
        abort(403)
    if request.method == "POST":
        nueva_pieza = {
            "nombre": request.form.get("nombre", "").strip(),
            "marca": request.form.get("marca", "").strip(),
            "modelo": request.form.get("modelo", "").strip(),
            "anio": request.form.get("anio", "").strip(),
            "categoria": request.form.get("categoria", "").strip(),
            "precio": float(request.form.get("precio", 0)),
            "existencias": int(request.form.get("existencias", 10)),
            "reservadas": 0,
            "estado": request.form.get("estado", "Disponible"),
            "imagen": request.form.get("imagen", "").strip() or "https://via.placeholder.com/300x200?text=Refaccion",
            "descripcion": request.form.get("descripcion", "").strip()
        }
        piezas.insert_one(nueva_pieza)
        return redirect(url_for("admin_inventario"))
    return render_template("admin_agregar.html")

@app.route("/admin/editar/<id>", methods=["GET", "POST"])
def admin_editar(id):
    if not es_admin():
        abort(403)
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)
    pieza = piezas.find_one({"_id": oid})
    if not pieza:
        abort(404)

    if request.method == "POST":
        piezas.update_one({"_id": oid}, {"$set": {
            "nombre": request.form.get("nombre", "").strip(),
            "marca": request.form.get("marca", "").strip(),
            "modelo": request.form.get("modelo", "").strip(),
            "anio": request.form.get("anio", "").strip(),
            "categoria": request.form.get("categoria", "").strip(),
            "precio": float(request.form.get("precio", 0)),
            "existencias": int(request.form.get("existencias", pieza.get("existencias", 10))),
            "estado": request.form.get("estado", "Disponible"),
            "imagen": request.form.get("imagen", "").strip(),
            "descripcion": request.form.get("descripcion", "").strip()
        }})
        return redirect(url_for("admin_inventario"))
    return render_template("admin_editar.html", pieza=pieza)

@app.route("/admin/eliminar/<id>", methods=["POST"])
def admin_eliminar(id):
    if not es_admin():
        abort(403)
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)
    piezas.delete_one({"_id": oid})
    return redirect(url_for("admin_inventario"))

@app.route("/ayuda")
def ayuda():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    return render_template("ayuda.html", fecha=fecha)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)