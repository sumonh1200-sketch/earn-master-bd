
import os
import secrets
from decimal import Decimal, InvalidOperation
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "change-this-secret-key"
)

database_url = os.environ.get("DATABASE_URL", "sqlite:///earn_master.db")

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# সব টাকা পয়সায় সংরক্ষণ হবে।
JOIN_BONUS = 200
REFERRAL_BONUS = 200
MIN_WITHDRAWAL = 10000
REQUIRED_REFERRALS = 3

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PAYMENT_METHODS = ["bkash", "nagad", "rocket"]


# =========================
# MONEY HELPERS
# =========================

def to_paisa(value):
    try:
        amount = Decimal(str(value).strip())

        if not amount.is_finite() or amount <= 0:
            return None

        if amount.as_tuple().exponent < -2:
            return None

        return int(amount * 100)

    except (InvalidOperation, ValueError, TypeError):
        return None


@app.template_filter("money")
def money_filter(value):
    try:
        amount = Decimal(str(value or 0)) / Decimal("100")
        return f"{amount:,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return "0.00"


# =========================
# DATABASE MODELS
# =========================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    referral_code = db.Column(db.String(30), unique=True, nullable=False)
    referred_by = db.Column(db.String(30), nullable=True)
    balance = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    method = db.Column(db.String(20), nullable=False)
    account_number = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="withdrawals")

    @property
    def account(self):
        return self.account_number


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    reward = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)


class TaskClaim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="task_claims")
    task = db.relationship("Task", backref="claims")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "task_id",
            name="unique_user_task_claim"
        ),
    )


class BalanceTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    admin_username = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(250), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="balance_transactions")


class MoneyMigration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(50), unique=True, nullable=False)


# =========================
# OLD MONEY DATA MIGRATION
# =========================

def migrate_old_money_to_paisa():
    version = "money_to_paisa_v1"

    if MoneyMigration.query.filter_by(version=version).first():
        return

    # পুরোনো টাকার পরিমাণকে পয়সায় রূপান্তর।
    db.session.execute(
        text("UPDATE \"user\" SET balance = balance * 100")
    )
    db.session.execute(
        text("UPDATE withdrawal SET amount = amount * 100")
    )
    db.session.execute(
        text("UPDATE task SET reward = reward * 100")
    )
    db.session.execute(
        text("UPDATE balance_transaction SET amount = amount * 100")
    )

    db.session.add(MoneyMigration(version=version))
    db.session.commit()


# =========================
# LOGIN HELPERS
# =========================

def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    return db.session.get(User, user_id)


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))

        return function(*args, **kwargs)

    return wrapper


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return render_template(
        "home.html",
        join_bonus=Decimal("2.00"),
        min_withdrawal=Decimal("100.00"),
        required_referrals=REQUIRED_REFERRALS
    )


# =========================
# REGISTER
# =========================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        referral = request.form.get("referral", "").strip()

        if not username or not password:
            flash("সব তথ্য পূরণ করুন।", "error")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("পাসওয়ার্ড কমপক্ষে ৬ অক্ষরের হতে হবে।", "error")
            return redirect(url_for("register"))

        if User.query.filter_by(username=username).first():
            flash("এই ইউজারনেম আগে থেকেই ব্যবহার করা হয়েছে।", "error")
            return redirect(url_for("register"))

        referrer = None

        if referral:
            referrer = User.query.filter_by(
                referral_code=referral
            ).first()

            if not referrer:
                flash("রেফারেল কোড সঠিক নয়।", "error")
                return redirect(url_for("register"))

        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            referral_code=secrets.token_hex(5).upper(),
            referred_by=referrer.referral_code if referrer else None,
            balance=JOIN_BONUS
        )

        db.session.add(new_user)

        if referrer:
            referrer.balance += REFERRAL_BONUS

        db.session.commit()

        flash("রেজিস্ট্রেশন সফল হয়েছে। এখন লগইন করুন।", "success")
        return redirect(url_for("login"))

    return render_template(
        "register.html",
        ref=request.args.get("ref", "")
    )


# =========================
# LOGIN / LOGOUT
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            return redirect(url_for("dashboard"))

        flash("ইউজারনেম অথবা পাসওয়ার্ড ভুল।", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# =========================
# USER DASHBOARD
# =========================

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    referrals = User.query.filter_by(
        referred_by=user.referral_code
    ).count()

    tasks = Task.query.filter_by(active=True).all()

    claims = {
        claim.task_id: claim.status
        for claim in TaskClaim.query.filter_by(user_id=user.id).all()
    }

    referral_url = url_for(
        "register", ref=user.referral_code, _external=True
    )

    return render_template(
        "dashboard.html",
        user=user,
        referral_count=referrals,
        referral_url=referral_url,
        tasks=tasks,
        claims=claims
    )


# =========================
# TASK CLAIM
# =========================

@app.route("/task/<int:task_id>/claim", methods=["POST"])
@login_required
def claim_task(task_id):
    user = current_user()
    task = db.session.get(Task, task_id)

    if not task or not task.active:
        flash("টাস্ক পাওয়া যায়নি।", "error")
        return redirect(url_for("dashboard"))

    existing = TaskClaim.query.filter_by(
        user_id=user.id,
        task_id=task.id
    ).first()

    if existing:
        flash("এই টাস্কের জন্য আগেই আবেদন করেছেন।", "error")
        return redirect(url_for("dashboard"))

    db.session.add(TaskClaim(
        user_id=user.id,
        task_id=task.id,
        status="pending"
    ))

    db.session.commit()

    flash("টাস্ক জমা হয়েছে। অ্যাডমিন যাচাই করবেন।", "success")
    return redirect(url_for("dashboard"))


# =========================
# WITHDRAW
# =========================

@app.route("/withdraw", methods=["POST"])
@login_required
def withdraw():
    user = current_user()
    amount = to_paisa(request.form.get("amount", ""))

    method = request.form.get("method", "").lower().strip()
    account = request.form.get("account", "").strip()

    referral_count = User.query.filter_by(
        referred_by=user.referral_code
    ).count()

    if amount is None:
        flash("সঠিক টাকার পরিমাণ লিখুন। যেমন 100.50", "error")
    elif amount < MIN_WITHDRAWAL:
        flash("সর্বনিম্ন উত্তোলন ১০০ টাকা।", "error")
    elif referral_count < REQUIRED_REFERRALS:
        flash("উত্তোলনের আগে ৩ জন সফল রেফারেল লাগবে।", "error")
    elif amount > user.balance:
        flash("আপনার ব্যালেন্স পর্যাপ্ত নয়।", "error")
    elif method not in PAYMENT_METHODS:
        flash("পেমেন্ট মেথড সঠিক নয়।", "error")
    elif not account:
        flash("পেমেন্ট অ্যাকাউন্ট নম্বর লিখুন।", "error")
    else:
        user.balance -= amount

        db.session.add(Withdrawal(
            user_id=user.id,
            amount=amount,
            method=method,
            account_number=account,
            status="pending"
        ))

        db.session.commit()
        flash("উইথড্র আবেদন জমা হয়েছে।", "success")

    return redirect(url_for("dashboard"))


# =========================
# ADMIN LOGIN
# =========================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if (
            ADMIN_PASSWORD
            and username == ADMIN_USERNAME
            and password == ADMIN_PASSWORD
        ):
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin"))

        flash("অ্যাডমিন তথ্য সঠিক নয়।", "error")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))


# =========================
# ADMIN PANEL
# =========================

@app.route("/admin")
@admin_required
def admin():
    withdrawals = Withdrawal.query.order_by(
        Withdrawal.created_at.desc()
    ).all()

    claims = TaskClaim.query.order_by(
        TaskClaim.created_at.desc()
    ).all()

    tasks = Task.query.order_by(Task.id.desc()).all()
    users = User.query.order_by(User.id.desc()).all()

    transactions = BalanceTransaction.query.order_by(
        BalanceTransaction.created_at.desc()
    ).all()

    return render_template(
        "admin.html",
        withdrawals=withdrawals,
        claims=claims,
        tasks=tasks,
        users=users,
        transactions=transactions
    )


# =========================
# ADMIN: ADD TASK
# =========================

@app.route("/admin/task", methods=["POST"])
@admin_required
def admin_add_task():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()

    reward = to_paisa(request.form.get("reward", ""))

    if not title or reward is None:
        flash(
            "টাস্কের নাম ও সঠিক পুরস্কার লিখুন। যেমন 0.50",
            "error"
        )
        return redirect(url_for("admin"))

    db.session.add(Task(
        title=title,
        description=description,
        reward=reward,
        active=True
    ))

    db.session.commit()

    flash("নতুন টাস্ক যোগ হয়েছে।", "success")
    return redirect(url_for("admin"))


# =========================
# ADMIN: ADD / REMOVE BALANCE
# =========================

@app.route("/admin/balance", methods=["POST"])
@admin_required
def admin_balance():
    try:
        user_id = int(request.form.get("user_id", "0"))
    except ValueError:
        user_id = 0

    amount = to_paisa(request.form.get("amount", ""))
    action = request.form.get("action", "").strip()
    reason = request.form.get("reason", "").strip()

    user = db.session.get(User, user_id)

    if not user:
        flash("ইউজার পাওয়া যায়নি।", "error")

    elif amount is None:
        flash("সঠিক টাকার পরিমাণ লিখুন। যেমন 0.50", "error")

    elif action not in ["add", "remove"]:
        flash("সঠিক অ্যাকশন নির্বাচন করুন।", "error")

    elif not reason:
        flash("ব্যালেন্স পরিবর্তনের কারণ লিখুন।", "error")

    elif action == "remove" and amount > user.balance:
        flash("ইউজারের ব্যালেন্সের চেয়ে বেশি কমানো যাবে না।", "error")

    else:
        if action == "add":
            user.balance += amount
        else:
            user.balance -= amount

        db.session.add(BalanceTransaction(
            user_id=user.id,
            admin_username=ADMIN_USERNAME,
            action=action,
            amount=amount,
            reason=reason
        ))

        db.session.commit()

        flash("ইউজারের ব্যালেন্স সফলভাবে আপডেট হয়েছে।", "success")

    return redirect(url_for("admin"))


# =========================
# ADMIN: WITHDRAWAL ACTION
# =========================

@app.route("/admin/withdraw/<int:wid>", methods=["POST"])
@admin_required
def admin_withdraw(wid):
    withdrawal = db.session.get(Withdrawal, wid)

    if not withdrawal:
        flash("উইথড্র আবেদন পাওয়া যায়নি।", "error")
        return redirect(url_for("admin"))

    action = request.form.get("action", "")

    if withdrawal.status != "pending":
        flash("এই আবেদনটি আগেই প্রক্রিয়া করা হয়েছে।", "error")

    elif action == "approve":
        withdrawal.status = "approved"
        flash("উইথড্র অনুমোদন করা হয়েছে।", "success")

    elif action == "reject":
        withdrawal.status = "rejected"
        withdrawal.user.balance += withdrawal.amount
        flash("উইথড্র বাতিল হয়েছে এবং টাকা ফেরত দেওয়া হয়েছে।", "success")

    db.session.commit()
    return redirect(url_for("admin"))


# =========================
# ADMIN: TASK CLAIM ACTION
# =========================

@app.route("/admin/claim/<int:cid>", methods=["POST"])
@admin_required
def admin_claim(cid):
    claim = db.session.get(TaskClaim, cid)

    if not claim:
        flash("টাস্ক আবেদন পাওয়া যায়নি।", "error")
        return redirect(url_for("admin"))

    action = request.form.get("action", "")

    if claim.status != "pending":
        flash("এই আবেদনটি আগেই যাচাই করা হয়েছে।", "error")

    elif action == "approve":
        claim.status = "approved"
        claim.user.balance += claim.task.reward
        flash("টাস্ক অনুমোদন হয়েছে এবং পুরস্কার যোগ হয়েছে।", "success")

    elif action == "reject":
        claim.status = "rejected"
        flash("টাস্ক আবেদন বাতিল হয়েছে।", "success")

    db.session.commit()
    return redirect(url_for("admin"))


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():
    return {"status": "ok"}, 200


# =========================
# CREATE TABLES AND MIGRATE
# =========================

with app.app_context():
    db.create_all()

    try:
        migrate_old_money_to_paisa()
    except Exception:
        db.session.rollback()
        app.logger.exception("Money migration failed")
        raise


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
