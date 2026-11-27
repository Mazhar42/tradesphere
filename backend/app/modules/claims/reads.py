"""Response builders for claims."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.claims.models import (
    ClaimableItem,
    ClaimSettlement,
    CommissionPayment,
    CompanyClaim,
)
from app.modules.claims.schemas import (
    ClaimableRead,
    ClaimLineRead,
    ClaimSettlementRead,
    CommissionRead,
    CompanyClaimListItem,
    CompanyClaimRead,
)


def claimable_read(item: ClaimableItem, supplier_name: str) -> ClaimableRead:
    return ClaimableRead(
        id=item.id,
        supplier_id=item.supplier_id,
        supplier_name=supplier_name,
        claim_type=item.claim_type,
        source_doc_type=item.source_doc_type,
        source_doc_id=item.source_doc_id,
        source_line_id=item.source_line_id,
        business_date=item.business_date,
        book_amount=item.book_amount,
        claim_amount=item.claim_amount,
        status=item.status,
    )


def settlement_read(settlement: ClaimSettlement) -> ClaimSettlementRead:
    return ClaimSettlementRead(
        id=settlement.id,
        status=settlement.status,
        doc_no=settlement.doc_no,
        business_date=settlement.business_date,
        method=settlement.method,
        amount=settlement.amount,
        payment_account_name=settlement.payment_account.name
        if settlement.payment_account
        else None,
        reference=settlement.reference,
        notes=settlement.notes,
    )


def claim_to_read(session: Session, claim: CompanyClaim) -> CompanyClaimRead:
    settlements = session.scalars(
        select(ClaimSettlement)
        .where(ClaimSettlement.claim_id == claim.id)
        .order_by(ClaimSettlement.id)
    ).all()
    return CompanyClaimRead(
        id=claim.id,
        doc_no=claim.doc_no,
        supplier_id=claim.supplier_id,
        supplier_name=claim.supplier.name,
        business_date=claim.business_date,
        period_from=claim.period_from,
        period_to=claim.period_to,
        status=claim.status,
        company_ref=claim.company_ref,
        notes=claim.notes,
        total_claimed=claim.total_claimed,
        total_settled=claim.total_settled,
        total_rejected=claim.total_rejected,
        outstanding=claim.total_claimed - claim.total_settled - claim.total_rejected,
        submitted_at=claim.submitted_at,
        lines=[
            ClaimLineRead(
                id=line.id,
                claimable_item_id=line.claimable_item_id,
                claim_type=line.claimable_item.claim_type,
                source_doc_type=line.claimable_item.source_doc_type,
                source_doc_id=line.claimable_item.source_doc_id,
                business_date=line.claimable_item.business_date,
                book_amount=line.book_amount,
                claimed_amount=line.claimed_amount,
                settled_amount=line.settled_amount,
                rejected_amount=line.rejected_amount,
                outstanding_amount=line.claimed_amount - line.settled_amount - line.rejected_amount,
            )
            for line in claim.lines
        ],
        settlements=[settlement_read(s) for s in settlements],
    )


def claim_list_item(claim: CompanyClaim, supplier_name: str) -> CompanyClaimListItem:
    return CompanyClaimListItem(
        id=claim.id,
        doc_no=claim.doc_no,
        supplier_id=claim.supplier_id,
        supplier_name=supplier_name,
        business_date=claim.business_date,
        status=claim.status,
        total_claimed=claim.total_claimed,
        total_settled=claim.total_settled,
        total_rejected=claim.total_rejected,
        outstanding=claim.total_claimed - claim.total_settled - claim.total_rejected,
    )


def commission_read(payment: CommissionPayment) -> CommissionRead:
    return CommissionRead(
        id=payment.id,
        status=payment.status,
        doc_no=payment.doc_no,
        customer_id=payment.customer_id,
        customer_name=payment.customer.name,
        business_date=payment.business_date,
        amount=payment.amount,
        method=payment.method,
        payment_account_name=payment.payment_account.name if payment.payment_account else None,
        is_company_funded=payment.is_company_funded,
        supplier_id=payment.supplier_id,
        scheme_ref=payment.scheme_ref,
        claimable_item_id=payment.claimable_item_id,
    )
