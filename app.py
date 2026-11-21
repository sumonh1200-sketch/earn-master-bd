
import os
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "CHANGE-THIS-SECRET")

database_url = os.environ.get(
    "DATABASE_URL", "sqlite:///earn_master_bd.db"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

JOIN_BONUS = 2
MIN_WITHDRAWAL = 100
REQUIRED_REFERRALS = 3

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    referral_code = db.Column(db.String(20), unique=True, nullable=False)
    referred_by = db.Column(db.String(20))
    balance = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    method = db.Column(db.String(20), nullable=False)
    account_number = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="withdrawals")


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    reward = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, default=True)


class TaskClaim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")

    user = db.relationship("User")
    task = db.relationship("Task")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "task_id", name="unique_user_task"
        ),
    )


def me():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not me():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped


@app.context_processor
def common():
    return {
        "logged_user": me(),
        "site_name": "Earn Master BD"
    }


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        ref = request.form.get("referral_code", "").strip()

        if len(username) < 3 or len(username) > 40 or len(password) < 8:
            flash("ইউজারনেম ৩ অক্ষর এবং পাসওয়ার্ড ৮ অক্ষরের হতে হবে।", "error")
            return redirect(url_for("register"))

        if User.query.filter_by(username=username).first():
            flash("এই ইউজারনেম আগে থেকেই আছে।", "error")
            return redirect(url_for("register"))

        parent = User.query.filter_by(
            referral_code=ref
        ).first() if ref else None

        code = secrets.token_urlsafe(8)[:12]

        while User.query.filter_by(referral_code=code).first():
            code = secrets.token_urlsafe(8)[:12]

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            referral_code=code,
            referred_by=parent.referral_code if parent else None,
            balance=JOIN_BONUS
        )

        db.session.add(user)
        db.session.commit()

        session["user_id"] = user.id

        flash("অ্যাকাউন্ট তৈরি হয়েছে। ৳২ বোনাস পেয়েছেন।", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "register.html",
        ref=request.args.get("ref", "")
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            return redirect(url_for("dashboard"))

        flash("লগইন তথ্য সঠিক নয়।", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = me()

    referrals = User.query.filter_by(
        referred_by=user.referral_code
    ).count()

    tasks = Task.query.filter_by(active=True).all()

    claims = {
        c.task_id: c.status
        for c in TaskClaim.query.filter_by(user_id=user.id).all()
    }

    return render_template(
        "dashboard.html",
        u=user,
        refs=referrals,
        tasks=tasks,
        claims=claims
    )


@app.post("/task/<int:tid>/claim")
@login_required
def claim(tid):
    user = me()
    task = db.session.get(Task, tid)

    if not task or not task.active:
        flash("কাজটি পাওয়া যায়নি।", "error")

    elif TaskClaim.query.filter_by(
        user_id=user.id, task_id=tid
    ).first():
        flash("এই কাজের আবেদন আগে হয়েছে।", "error")

    else:
        db.session.add(
            TaskClaim(user_id=user.id, task_id=tid)
        )
        db.session.commit()

        flash("আবেদন জমা হয়েছে। অ্যাডমিন যাচাই করবেন।", "success")

    return redirect(url_for("dashboard"))


@app.post("/withdraw")
@login_required
def withdraw():
    user = me()

    referrals = User.query.filter_by(
        referred_by=user.referral_code
    ).count()

    try:
        amount = int(request.form.get("amount", "0"))
    except (ValueError, TypeError):
        amount = 0

    method = request.form.get("method", "").lower()
    account = request.form.get("account_number", "").strip()

    if referrals < REQUIRED_REFERRALS:
        flash("উত্তোলনের আগে ৩ জন সফল রেফার পূর্ণ করুন।", "error")

    elif amount < MIN_WITHDRAWAL:
        flash("সর্বনিম্ন উত্তোলন ৳১০০।", "error")

    elif amount > user.balance:
        flash("আপনার ব্যালেন্স যথেষ্ট নয়।", "error")

    elif method not in ("bkash", "nagad", "rocket") or not account:
        flash("সঠিক পেমেন্ট মাধ্যম ও নম্বর দিন।", "error")

    else:
        user.balance -= amount

        db.session.add(
            Withdrawal(
                user_id=user.id,
                amount=amount,
                method=method,
                account_number=account
            )
        )

        db.session.commit()
        flash("উত্তোলনের আবেদন জমা হয়েছে।", "success")

    return redirect(url_for("dashboard"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if ADMIN_PASSWORD and username == ADMIN_USERNAME and secrets.compare_digest(
            password, ADMIN_PASSWORD
        ):
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin"))

        flash("অ্যাডমিন লগইন ব্যর্থ।", "error")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin():
    return render_template(
        "admin.html",
        users=User.query.order_by(User.id.desc()).limit(200).all(),
        withdrawals=Withdrawal.query.filter_by(status="pending").all(),
        claims=TaskClaim.query.filter_by(status="pending").all()
    )


@app.post("/admin/withdraw/<int:wid>/<action>")
@admin_required
def admin_withdraw(wid, action):
    item = db.session.get(Withdrawal, wid)

    if item and item.status == "pending" and action in ("approve", "reject"):
        item.status = action

        if action == "reject":
            item.user.balance += item.amount

        db.session.commit()

    return redirect(url_for("admin"))


@app.post("/admin/claim/<int:cid>/<action>")
@admin_required
def admin_claim(cid, action):
    claim = db.session.get(TaskClaim, cid)

    if claim and claim.status == "pending" and action in ("approve", "reject"):
        claim.status = action

        if action == "approve":
            claim.user.balance += claim.task.reward

        db.session.commit()

    return redirect(url_for("admin"))


@app.post("/admin/task")
@admin_required
def add_task():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()

    try:
        reward = int(request.form.get("reward", "0"))
    except (ValueError, TypeError):
        reward = 0

    if title and description and 1 <= reward <= 1000:
        db.session.add(
            Task(
                title=title,
                description=description,
                reward=reward
            )
        )
        db.session.commit()
        flash("নতুন কাজ যোগ হয়েছে।", "success")
    else:
        flash("সঠিক কাজের তথ্য দিন।", "error")

    return redirect(url_for("admin"))


@app.get("/health")
def health():
    return {"ok": True, "service": "Earn Master BD"}


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
