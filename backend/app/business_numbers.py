"""Concurrency-safe business number allocation for supported databases."""
from datetime import date
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from .models import BusinessSequence

PREFIXES={"ticket":"DMTKT","enquiry":"DMENQ","job_card":"DMJC","invoice":"INV","certificate":"CERT","action":"ACT"}

def next_business_number(db:Session,kind:str,today:date|None=None)->str:
    prefix=PREFIXES.get(kind)
    if not prefix: raise ValueError("Unsupported business number type.")
    year=(today or date.today()).year
    key=f"{kind}:{year}"
    table=BusinessSequence.__table__
    dialect=db.get_bind().dialect.name
    if dialect=="postgresql":
        statement=pg_insert(table).values(key=key,value=1).on_conflict_do_update(index_elements=[table.c.key],set_={"value":table.c.value+1}).returning(table.c.value)
        value=db.execute(statement).scalar_one()
    elif dialect=="sqlite":
        statement=sqlite_insert(table).values(key=key,value=1).on_conflict_do_update(index_elements=[table.c.key],set_={"value":table.c.value+1}).returning(table.c.value)
        value=db.execute(statement).scalar_one()
    else:
        row=db.get(BusinessSequence,key,with_for_update=True)
        if row is None:
            row=BusinessSequence(key=key,value=1);db.add(row);db.flush();value=1
        else:
            value=row.value+1;db.execute(update(BusinessSequence).where(BusinessSequence.key==key).values(value=value))
    return f"{prefix}-{year}-{value:05d}"
