from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, Column, String, Float, Integer, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import ForeignKey
from pydantic import BaseModel
from typing import Optional, List
import uuid, datetime, hashlib, os

# ═══ DATABASE ═══
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "optimarket.db")
engine   = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
Session_ = sessionmaker(bind=engine)
Base     = declarative_base()

def get_db():
    db = Session_()
    try:
        yield db
    finally:
        db.close()

# ═══ MODELS ═══
class UserDB(Base):
    __tablename__ = "users"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name       = Column(String, nullable=False)
    login      = Column(String, unique=True, nullable=False)
    password   = Column(String, nullable=False)  # hashed
    role       = Column(String, nullable=False)
    balance    = Column(Float,  default=0.0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class CompanyDB(Base):
    __tablename__ = "companies"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name           = Column(String, nullable=False)
    desc           = Column(Text,   default="")
    emoji          = Column(String, default="🏢")
    owner_id       = Column(String, ForeignKey("users.id"), nullable=False)
    capital        = Column(Float,  default=0.0)
    markup_fund    = Column(Float,  default=0.0)
    markup         = Column(Float,  default=20.0)
    investor_limit = Column(Float,  default=49.0)
    created_at     = Column(DateTime, default=datetime.datetime.utcnow)
    investors      = relationship("InvestorDB", back_populates="company", cascade="all, delete")
    reports        = relationship("ReportDB",   back_populates="company", cascade="all, delete")

class InvestorDB(Base):
    __tablename__ = "investors"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    user_id    = Column(String, ForeignKey("users.id"),     nullable=False)
    effective  = Column(Float, default=0.0)
    invested   = Column(Float, default=0.0)
    share_pct  = Column(Float, default=0.0)
    company    = relationship("CompanyDB", back_populates="investors")

class ReportDB(Base):
    __tablename__ = "reports"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    title      = Column(String, nullable=False)
    amount     = Column(Float,  default=0.0)
    note       = Column(Text,   default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    company    = relationship("CompanyDB", back_populates="reports")

class PlatformDB(Base):
    __tablename__ = "platform"
    id       = Column(Integer, primary_key=True, default=1)
    earnings = Column(Float, default=0.0)

Base.metadata.create_all(engine)

# Создаём запись платформы если нет
with Session_() as s:
    if not s.get(PlatformDB, 1):
        s.add(PlatformDB(id=1, earnings=0.0))
        s.commit()

# ═══ UTILS ═══
PLATFORM_FEE = 0.01  # 1%

def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()

def total_investor_pct(company: CompanyDB) -> float:
    return sum(i.share_pct for i in company.investors)

def recompute_shares(company: CompanyDB):
    total_eff = sum(i.effective for i in company.investors)
    if total_eff == 0 or company.capital == 0:
        return
    for inv in company.investors:
        inv.share_pct = (inv.effective / company.capital) * 100

def company_to_dict(c: CompanyDB, db: Session) -> dict:
    owner = db.get(UserDB, c.owner_id)
    inv_pct = total_investor_pct(c)
    return {
        "id":             c.id,
        "name":           c.name,
        "desc":           c.desc,
        "emoji":          c.emoji,
        "ownerId":        c.owner_id,
        "ownerName":      owner.name if owner else "?",
        "capital":        c.capital,
        "markupFund":     c.markup_fund,
        "markup":         c.markup,
        "investorLimit":  c.investor_limit,
        "investorPct":    round(inv_pct, 2),
        "investorCount":  len(c.investors),
        "createdAt":      c.created_at.isoformat(),
        "investors": [
            {
                "uid":      i.user_id,
                "name":     db.get(UserDB, i.user_id).name if db.get(UserDB, i.user_id) else "?",
                "sharePct": round(i.share_pct, 4),
                "invested": i.invested,
                "effective":i.effective,
            }
            for i in c.investors
        ],
        "reports": [
            {
                "title": r.title,
                "amount": r.amount,
                "note":   r.note,
                "ts":     r.created_at.isoformat(),
            }
            for r in sorted(c.reports, key=lambda x: x.created_at, reverse=True)[:10]
        ],
    }

# ═══ SCHEMAS ═══
class RegisterReq(BaseModel):
    name:  str
    login: str
    password: str
    role:  str  # "investor" | "owner"

class LoginReq(BaseModel):
    login:    str
    password: str

class CreateCompanyReq(BaseModel):
    name:          str
    desc:          str
    emoji:         Optional[str] = "🏢"
    capital:       float
    markup:        float
    investorLimit: float

class InvestReq(BaseModel):
    userId:    str
    companyId: str
    amount:    float

class WithdrawReq(BaseModel):
    userId:    str
    companyId: str
    pct:       float  # % of their share to withdraw

class OwnerWithdrawReq(BaseModel):
    userId:    str
    companyId: str
    amount:    float

class AddCapitalReq(BaseModel):
    userId:    str
    companyId: str
    amount:    float

class ReportReq(BaseModel):
    userId:    str
    companyId: str
    title:     str
    amount:    float
    note:      Optional[str] = ""

class DepositReq(BaseModel):
    userId: str
    amount: float

# ═══ APP ═══
app = FastAPI(title="OptiMarket API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── AUTH ───
@app.post("/register")
def register(req: RegisterReq, db: Session = Depends(get_db)):
    if db.query(UserDB).filter_by(login=req.login).first():
        raise HTTPException(400, "Логин занят")
    if req.role not in ("investor", "owner"):
        raise HTTPException(400, "Неверная роль")
    u = UserDB(
        id=str(uuid.uuid4()),
        name=req.name,
        login=req.login,
        password=hash_pass(req.password),
        role=req.role,
        balance=0.0,
    )
    db.add(u); db.commit()
    return {"ok": True, "user": {"id": u.id, "name": u.name, "login": u.login, "role": u.role, "balance": u.balance}}

@app.post("/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    u = db.query(UserDB).filter_by(login=req.login).first()
    if not u or u.password != hash_pass(req.password):
        raise HTTPException(401, "Неверный логин или пароль")
    return {"ok": True, "user": {"id": u.id, "name": u.name, "login": u.login, "role": u.role, "balance": u.balance}}

@app.get("/user/{user_id}")
def get_user(user_id: str, db: Session = Depends(get_db)):
    u = db.get(UserDB, user_id)
    if not u: raise HTTPException(404, "Пользователь не найден")
    return {"id": u.id, "name": u.name, "login": u.login, "role": u.role, "balance": u.balance}

# ─── DEPOSIT (демо) ───
@app.post("/deposit")
def deposit(req: DepositReq, db: Session = Depends(get_db)):
    u = db.get(UserDB, req.userId)
    if not u: raise HTTPException(404, "Пользователь не найден")
    if req.amount < 1: raise HTTPException(400, "Минимум 1 ₽")
    u.balance += req.amount
    db.commit()
    return {"ok": True, "balance": u.balance}

# ─── COMPANIES ───
@app.get("/companies")
def get_companies(db: Session = Depends(get_db)):
    companies = db.query(CompanyDB).all()
    return [company_to_dict(c, db) for c in companies]

@app.get("/companies/{company_id}")
def get_company(company_id: str, db: Session = Depends(get_db)):
    c = db.get(CompanyDB, company_id)
    if not c: raise HTTPException(404, "Компания не найдена")
    return company_to_dict(c, db)

@app.post("/companies/create")
def create_company(req: CreateCompanyReq, user_id: str, db: Session = Depends(get_db)):
    u = db.get(UserDB, user_id)
    if not u: raise HTTPException(404, "Пользователь не найден")
    if u.role != "owner": raise HTTPException(403, "Только владелец может создавать компании")
    if u.balance < req.capital: raise HTTPException(400, "Недостаточно средств")
    u.balance -= req.capital
    c = CompanyDB(
        id=str(uuid.uuid4()),
        name=req.name, desc=req.desc, emoji=req.emoji or "🏢",
        owner_id=user_id, capital=req.capital,
        markup=req.markup, investor_limit=req.investorLimit,
    )
    db.add(c); db.commit(); db.refresh(c)
    return {"ok": True, "company": company_to_dict(c, db), "balance": u.balance}

# ─── INVEST ───
@app.post("/invest")
def invest(req: InvestReq, db: Session = Depends(get_db)):
    u = db.get(UserDB, req.userId)
    c = db.get(CompanyDB, req.companyId)
    if not u: raise HTTPException(404, "Пользователь не найден")
    if not c: raise HTTPException(404, "Компания не найдена")
    if req.amount < 1: raise HTTPException(400, "Минимум 1 ₽")
    if u.balance < req.amount: raise HTTPException(400, "Недостаточно средств")

    # Лимит инвесторов
    inv_pct = total_investor_pct(c)
    platform_fee = req.amount * PLATFORM_FEE
    sum_after_fee = req.amount - platform_fee
    effective = sum_after_fee / (1 + c.markup / 100)
    markup_fee = sum_after_fee - effective
    new_cap = c.capital + effective
    new_inv_pct = inv_pct + (effective / new_cap * 100) if new_cap > 0 else 0

    if new_inv_pct > c.investor_limit:
        raise HTTPException(400, f"Лимит инвесторов {c.investor_limit}% будет превышен")

    # Обновляем инвестора
    inv = next((i for i in c.investors if i.user_id == req.userId), None)
    if not inv:
        inv = InvestorDB(company_id=c.id, user_id=req.userId, effective=0, invested=0, share_pct=0)
        db.add(inv)
        c.investors.append(inv)
        db.flush()

    inv.effective += effective
    inv.invested  += req.amount
    c.capital     += effective
    c.markup_fund += markup_fee
    u.balance     -= req.amount

    # Комиссия платформы
    platform = db.get(PlatformDB, 1)
    platform.earnings += platform_fee

    recompute_shares(c)
    db.commit()

    return {
        "ok": True,
        "sharePct": round(inv.share_pct, 4),
        "balance":  u.balance,
        "platformFee": platform_fee,
        "company":  company_to_dict(c, db),
    }

# ─── INVESTOR WITHDRAW ───
@app.post("/withdraw")
def withdraw(req: WithdrawReq, db: Session = Depends(get_db)):
    u = db.get(UserDB, req.userId)
    c = db.get(CompanyDB, req.companyId)
    if not u or not c: raise HTTPException(404, "Не найдено")
    if req.pct < 1 or req.pct > 100: raise HTTPException(400, "От 1 до 100%")

    inv = next((i for i in c.investors if i.user_id == req.userId), None)
    if not inv: raise HTTPException(404, "Ты не инвестор этой компании")

    cur_val  = c.capital * inv.share_pct / 100
    out      = cur_val * req.pct / 100
    if c.capital < out: raise HTTPException(400, "Недостаточно средств в компании")

    c.capital      -= out
    inv.effective  = max(0, inv.effective * (1 - req.pct / 100))
    inv.invested   = max(0, inv.invested  * (1 - req.pct / 100))
    u.balance      += out

    recompute_shares(c)
    inv.share_pct *= (1 - req.pct / 100)
    db.commit()

    return {"ok": True, "received": out, "balance": u.balance, "company": company_to_dict(c, db)}

# ─── OWNER WITHDRAW (markup fund) ───
@app.post("/owner-withdraw")
def owner_withdraw(req: OwnerWithdrawReq, db: Session = Depends(get_db)):
    u = db.get(UserDB, req.userId)
    c = db.get(CompanyDB, req.companyId)
    if not u or not c: raise HTTPException(404, "Не найдено")
    if c.owner_id != req.userId: raise HTTPException(403, "Не твоя компания")
    if req.amount < 1: raise HTTPException(400, "Минимум 1 ₽")
    if req.amount > c.markup_fund: raise HTTPException(400, f"В фонде только {c.markup_fund:.0f} ₽")

    c.markup_fund -= req.amount
    u.balance     += req.amount
    db.commit()

    return {"ok": True, "received": req.amount, "balance": u.balance, "markupFund": c.markup_fund}

# ─── ADD CAPITAL ───
@app.post("/add-capital")
def add_capital(req: AddCapitalReq, db: Session = Depends(get_db)):
    u = db.get(UserDB, req.userId)
    c = db.get(CompanyDB, req.companyId)
    if not u or not c: raise HTTPException(404, "Не найдено")
    if c.owner_id != req.userId: raise HTTPException(403, "Не твоя компания")
    if u.balance < req.amount: raise HTTPException(400, "Недостаточно средств")

    u.balance  -= req.amount
    c.capital  += req.amount
    recompute_shares(c)
    db.commit()

    return {"ok": True, "capital": c.capital, "balance": u.balance}

# ─── REPORT ───
@app.post("/report")
def add_report(req: ReportReq, db: Session = Depends(get_db)):
    c = db.get(CompanyDB, req.companyId)
    if not c: raise HTTPException(404, "Компания не найдена")
    if c.owner_id != req.userId: raise HTTPException(403, "Не твоя компания")
    r = ReportDB(company_id=c.id, title=req.title, amount=req.amount, note=req.note or "")
    db.add(r); db.commit()
    return {"ok": True}

# ─── PLATFORM STATS ───
@app.get("/platform/earnings")
def platform_earnings(db: Session = Depends(get_db)):
    p = db.get(PlatformDB, 1)
    return {"earnings": p.earnings if p else 0}

@app.get("/")
def root():
    return {"status": "OptiMarket API работает ✅"}
