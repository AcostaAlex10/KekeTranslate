# Grabar desde el movil

La app corre en tu PC y el movil entra por la red local. Nada sale a internet.

## Por que hace falta un certificado

Los navegadores solo dan acceso al microfono en **contexto seguro**: `https://`
o `localhost`. Si el movil entra por `http://192.168.1.34:8501`, Chrome deniega
el microfono sin llegar a preguntar. No es configurable: por eso la app se
sirve por HTTPS con un certificado propio, y el movil tiene que confiar en el.

## 1. Generar los certificados (una vez, en la PC)

Ya estan en `certs/`, generados para la IP **192.168.1.34**. Si tu IP cambia,
hay que rehacer el certificado del servidor:

```bash
cd certs && openssl req -newkey rsa:2048 -nodes -keyout server-key.pem -out server.csr -subj "//CN=TU_IP" && printf 'subjectAltName=IP:TU_IP,IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > server-ext.cnf && openssl x509 -req -in server.csr -CA ca-cert.pem -CAkey ca-key.pem -CAcreateserial -out server-cert.pem -days 820 -sha256 -extfile server-ext.cnf && rm server.csr
```

La CA (`ca-cert.pem` / `ca-key.pem`) se reutiliza: **no hay que reinstalar nada
en el movil** si solo cambia la IP.

Conviene reservar la IP en el router para no repetir esto cada dos por tres.

`certs/` esta en `.gitignore`. La clave privada no debe subirse nunca al repo.

## 2. Instalar la CA en el movil (una vez, Android)

1. Pasa `certs/keke-ca.crt` al telefono (cable, Bluetooth o correo a ti mismo).
2. **Ajustes → Seguridad → Cifrado y credenciales → Instalar un certificado →
   Certificado de CA**. Android avisa de que "tu red podria estar
   monitorizada": es el aviso normal al instalar una CA propia.
3. Elige el fichero. Deberia aparecer como *KekeTranslate CA local*.

Chrome en Android confia en las CA instaladas por el usuario, asi que con esto
deja de salir la advertencia.

## 3. Arrancar y entrar

Por defecto la app arranca **sin HTTPS y solo para esta maquina**, que es lo
comodo para desarrollar: en `localhost` el navegador ya da acceso al microfono.

Para que entre el movil hay que arrancarla en modo red, con los certificados:

```bash
cd "E:\claude code\keketranslate"; .\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.address 0.0.0.0 --server.sslCertFile certs/server-cert.pem --server.sslKeyFile certs/server-key.pem
```

Y el backend en otra terminal:

```bash
cd "E:\claude code\keketranslate"; .\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
```

Desde el movil, en la misma WiFi: **https://192.168.1.34:8501**

El backend (puerto 8000) no necesita estar expuesto: quien le habla es
Streamlit desde la propia PC, no el navegador del movil.

## Esto no sirve para usuarios finales

Instalar una CA a mano en Ajustes es aceptable para quien desarrolla, no para
un companero de clase. Para que otros usen la app hace falta servirla desde
internet con un certificado de verdad (Let's Encrypt), y entonces el movil no
tiene que instalar nada.

Eso adelanta una decision: en cuanto la app este en internet, los apuntes
privados necesitan autenticacion de verdad. Hoy no hay ninguna.

## Problemas conocidos

**Norton intercepta el HTTPS.** En esta PC, Norton Web/Mail Shield sustituye el
certificado del servidor por uno suyo (`Norton Web/Mail Shield Untrusted Root`)
en las conexiones locales. Si en el movil aparece una advertencia de
certificado y el emisor es Norton, hay que excluir el puerto 8501 en la
configuracion de Norton. La cadena propia es correcta; se puede comprobar con:

```bash
cd certs && openssl verify -CAfile ca-cert.pem server-cert.pem
```

**El cortafuegos de Windows** puede bloquear el puerto 8501 para equipos de la
red. Si el movil no llega a cargar la pagina, hay que permitir el puerto para
redes privadas.

**La grabadora actual es provisional.** Guarda el audio sin comprimir y no lo
envia hasta que paras, asi que sirve para clases cortas. Para una clase larga,
graba con la app del movil y subela como fichero.
