import math
from flask import current_app, g, Blueprint, session, render_template, redirect, url_for, flash
from decimal import Decimal

from auth import login_required
from database import get_connection, get_cursor
from forms import CampaignCreationForm, CampaignEditForm, DonationForm, SearchForm


bp = Blueprint('campaign', __name__, url_prefix='/campaign')


@bp.route('/create', methods=('GET', 'POST'))
@login_required
def create_campaign():
    form = CampaignCreationForm()
    connection = get_connection()
    cursor = get_cursor()
    if form.validate_on_submit():
        try:
            with connection:
                with cursor:
                    cursor.execute("""INSERT INTO campaign(name, description, image, amount_requested) 
                                      VALUES (%s, %s, %s, %s) RETURNING id;""",
                                   (form.name.data, form.description.data, form.image.data, form.amount_requested.data))

                    id = cursor.fetchone()[0]

                    cursor.execute("""INSERT INTO campaign_relation(user_account_id, campaign_id, user_role) 
                                      VALUES (%s, %s, %s); """,
                                   (session['user_id'], id, 'owner'))
                    return redirect(url_for("campaign.view_campaign", id=id))
        except Exception as e:
            current_app.logger.error(e)

    return render_template("campaign/create.html", form=form)


@bp.route('/edit/<int:id>', methods=('GET', 'POST'))
@login_required
def edit_campaign(id):
    connection = get_connection()
    cursor = get_cursor()
    cursor.execute("""SELECT name, description, image, amount_requested FROM campaign WHERE id=%s;""",(id,))
    curr_campaign = cursor.fetchone()
    curr_campaign['amount_requested'] = Decimal(curr_campaign['amount_requested'].replace(",","").replace("$",""))
    curr_campaign = dict(zip(list(curr_campaign._index.keys()), list(curr_campaign)))
    current_app.logger.info(curr_campaign)
    form = CampaignEditForm(**curr_campaign)
    owner_id = None

    try:
        cursor.execute("""SELECT user_account_id FROM campaign_relation WHERE campaign_id=%s AND user_role='owner'""",
                       (id,))
        owner_id = cursor.fetchone()[0]

    except Exception as e:
        current_app.logger.error(e)

    if owner_id != session['user_id']:
        flash("You cannot edit a campaign you don't own!", 'error')
        return redirect(url_for("campaign.view_campaign", id=id))

    if form.validate_on_submit():
        try:
            with connection:
                with cursor:
                    cursor.execute("""UPDATE campaign SET (name, description, image, amount_requested) =  
                                      (%s, %s, %s, %s) WHERE id=%s;""",
                                   (form.name.data, form.description.data, form.image.data,
                                    form.amount_requested.data, form.campaign_id.data))
            flash("Succesully updated!", 'success')
            return redirect(url_for("campaign.view_campaign", id=id))
        except Exception as e:
            current_app.logger.error(e)

    return render_template("campaign/edit.html", form=form, id=id)


@bp.route('/<int:id>', methods=('GET', 'POST'))
def view_campaign(id):
    connection = get_connection()
    cursor = get_cursor()
    form = DonationForm()
    campaign = None
    amount_donated = None
    percentage = None
    donations = None

    def setup():
        nonlocal campaign, amount_donated, percentage, donations
        try:
            cursor.execute("""
                SELECT c.id AS campaign_id, c.name, c.description, c.image, c.amount_requested, c.date_created, 
                c.last_modified, up.id AS owner_id, up.first_name, up.last_name, up.profile_image, 
                up.description AS owner_description, 
                get_total_donations(c.id) AS amount_donated,
                ceil((get_total_donations(c.id)/c.amount_requested)*100) AS percentage
                FROM campaign c
                INNER JOIN campaign_relation cr on c.id = cr.campaign_id
                INNER JOIN user_account ua on cr.user_account_id = ua.id
                INNER JOIN user_profile up on ua.id = up.user_account_id WHERE c.id = %s AND cr.user_role='owner';
            """, (id,))

            campaign = cursor.fetchone()

            cursor.execute("""
                SELECT up.first_name, up.last_name, up.profile_image, t.amount
                FROM campaign c INNER JOIN campaign_relation cr ON c.id = cr.campaign_id
                INNER JOIN transaction t ON t.id = cr.transaction_id
                INNER JOIN user_profile up ON cr.user_account_id = up.user_account_id
                WHERE user_role='pledged' AND c.id=%s ORDER BY t.date_created DESC LIMIT 10;
            """, (id,))

            donations = cursor.fetchall()

        except Exception as e:
            current_app.logger.error(e)

    if form.validate_on_submit():
        try:
            connection = get_connection()
            cursor = get_cursor()
            with connection:
                with cursor:
                    cursor.execute("""
                        SELECT credit_card FROM user_profile WHERE user_account_id=%s
                    """, (session['user_id'],))


                    cc_number = cursor.fetchone()[0]


                    cursor.execute("""
                        INSERT INTO transaction(credit_card, amount) 
                        VALUES (%s, %s) RETURNING id;
                    """, (cc_number, form.amount.data))

                    transaction_id = cursor.fetchone()[0]

                    cursor.execute("""
                        INSERT INTO campaign_relation(user_account_id, campaign_id, transaction_id, user_role) 
                        VALUES (%s, %s, %s, %s)   
                    """, (session['user_id'], form.campaign_id.data, transaction_id, 'pledged'))

                    flash('Successfully donated!', 'success')
                    setup()
                    return render_template("campaign/campaign.html",
                                           form=form, campaign=campaign, donations=donations)
        except Exception as e:
            current_app.logger.error(e)
            flash(e, 'error')

    flash(form.errors, 'error')
    setup()
    return render_template("campaign/campaign.html",
                           form=form, campaign=campaign, donations=donations)


@bp.route('/search/', methods=('GET', 'POST'), defaults={'offset': 0})
@bp.route('/search/<int:offset>', methods=('GET', 'POST'))
def search_campaigns(offset):
    cursor = get_cursor()
    form = SearchForm()
    pages = None

    if form.validate_on_submit():
        current_app.logger.info("searching")
        query = " & ".join(form.search.data.split(' '))
        current_app.logger.info("query: " + query)
        cursor.execute("""
            SELECT c.name, c.description, c.image, c.id AS campaign_id, get_total_donations(c.id) AS amount_donated, 
            c.amount_requested, ceil((get_total_donations(c.id)/c.amount_requested)*100) AS percentage
            FROM campaign c
            INNER JOIN campaign_relation cr ON c.id = cr.campaign_id
            INNER JOIN user_account ua ON cr.user_account_id = ua.id
            INNER JOIN user_profile up ON ua.id = up.user_account_id where cr.user_role='owner'
            AND to_tsvector('english', 
            coalesce(c.description, '') || ' ' ||  
            coalesce(c.name, '') || ' ' || 
            coalesce(up.first_name, '') || ' ' || 
            coalesce(up.last_name, ''))
            @@ to_tsquery('english', %s) ORDER BY ts_rank_cd(
            to_tsvector('english', 
            coalesce(c.description, '') || ' ' || 
            coalesce(c.name, '') || ' ' || 
            coalesce(up.first_name, '') || ' ' || 
            coalesce(up.last_name, '')),
            to_tsquery('english', %s)) DESC;
        """, (query, query,))
        campaigns = cursor.fetchall();
        current_app.logger.info(str(campaigns))

        return render_template("campaign/gallery.html", search_term=form.search.data, campaigns=campaigns, form=form)

    cursor.execute("""
        SELECT COUNT(*) FROM campaign;
    """)
    pages = math.ceil(cursor.fetchone()[0] / 9)

    cursor.execute("""
        SELECT c.id AS campaign_id, c.name, c.description, c.image, c.amount_requested, c.date_created,                           
        c.last_modified, up.id AS owner_id, up.first_name, up.last_name, up.profile_image, 
        up.description AS owner_description, get_total_donations(c.id) AS amount_donated, 
        ceil((get_total_donations(c.id)/c.amount_requested)*100) AS percentage  
        FROM campaign c                                                                                                           
        INNER JOIN campaign_relation cr on c.id = cr.campaign_id                                                                
        INNER JOIN user_account ua on cr.user_account_id = ua.id                                                                
        INNER JOIN user_profile up on ua.id = up.user_account_id WHERE cr.user_role='owner' 
        ORDER BY c.date_created ASC OFFSET %s ROWS LIMIT 9;
        """, (offset*9,))

    campaigns = cursor.fetchall()
    return render_template("campaign/gallery.html", search_term=None, campaigns=campaigns, form=form, pages=pages)
