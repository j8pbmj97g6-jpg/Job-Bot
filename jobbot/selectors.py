"""Every CSS selector lives here.

LinkedIn and Naukri both rewrite their markup every few weeks. When the
collector suddenly returns zero jobs, this is almost always the only file
you need to touch. Each entry is a fallback chain, tried in order.
"""

LINKEDIN = {
    # The results list and its cards
    "results_container": [
        "div.jobs-search-results-list",
        "div.scaffold-layout__list > div",
        "ul.jobs-search__results-list",
    ],
    "job_card": [
        "li[data-occludable-job-id]",
        "div.job-card-container",
        "li.jobs-search-results__list-item",
    ],
    "job_id_attrs": ["data-occludable-job-id", "data-job-id"],
    "card_title": [
        "a.job-card-list__title--link strong",
        "a.job-card-list__title",
        "h3.base-search-card__title",
    ],
    "card_company": [
        "div.artdeco-entity-lockup__subtitle",
        "h4.base-search-card__subtitle",
        "a.job-card-container__company-name",
    ],
    "card_location": [
        "ul.job-card-container__metadata-wrapper li",
        "div.artdeco-entity-lockup__caption",
        "span.job-search-card__location",
    ],
    "card_footer": [
        "ul.job-card-list__footer-wrapper",
        "div.job-card-container__footer-wrapper",
    ],
    # Right-hand detail pane
    "detail_pane": [
        "div.jobs-search__job-details--container",
        "div.jobs-details",
        "div.job-view-layout",
    ],
    "detail_title": [
        "h1.job-title",
        "div.job-details-jobs-unified-top-card__job-title h1",
        "h2.top-card-layout__title",
    ],
    "detail_company": [
        "div.job-details-jobs-unified-top-card__company-name a",
        "div.job-details-jobs-unified-top-card__company-name",
    ],
    "detail_meta": [
        "div.job-details-jobs-unified-top-card__primary-description-container",
        "div.job-details-jobs-unified-top-card__tertiary-description-container",
    ],
    "detail_description": [
        "div#job-details",
        "div.jobs-description__content",
        "div.jobs-box__html-content",
    ],
    "see_more_button": [
        "button.jobs-description__footer-button",
        "button.show-more-less-html__button--more",
        "button[aria-label='Click to see more description']",
    ],
    # Usually a <button>, but some listings render it as an <a> instead
    # (confirmed live: 'aria=Easy Apply to this job | text=Easy Apply' on an
    # <a> element) -- without this, those jobs were wrongly treated as
    # requiring an external site, both at collection time (easy_apply=0,
    # so they'd be filtered out of the apply queue entirely) and at apply
    # time ("No Easy Apply button found").
    "easy_apply_button": [
        "button.jobs-apply-button",
        "button[aria-label*='Easy Apply']",
        "a[aria-label*='Easy Apply']",
    ],
    # Easy Apply modal (multi-step form)
    "modal_container": [
        "div.jobs-easy-apply-modal",
        "div[data-test-modal-id='easy-apply-modal']",
        "div.artdeco-modal--layer-default",
        "div.jobs-easy-apply-content",
        "div[role='dialog']",
        "div.artdeco-modal",
    ],
    "modal_heading": ["h3.t-16", "h2#jobs-apply-header"],
    "form_element": [
        "div.fb-dash-form-element",
        "div.jobs-easy-apply-form-section__grouping",
        "div.artdeco-text-input--container",
    ],
    "form_label": ["label", "legend", "span.artdeco-text-input--label"],
    # Type-ahead combobox (e.g. "Location (city)"): a plain .fill() sets the
    # visible text but not LinkedIn's required underlying selection --
    # "This field is required" persists. Must be checked BEFORE text_input,
    # since it also matches input[type='text']. NOTE: this field does NOT
    # carry role="combobox" in LinkedIn's real markup (confirmed via live
    # DevTools inspection after that guess silently never matched) --
    # data-testid="typeahead-input" is the real, stable marker. id is
    # dynamically generated («r13» etc.) and unusable as a selector.
    "combobox_input": ["input[data-testid='typeahead-input']", "input[role='combobox']"],
    "text_input": ["input[type='text']", "input[type='tel']", "input[type='number']", "textarea"],
    "select_input": ["select"],
    "radio_input": ["input[type='radio']"],
    "checkbox_input": ["input[type='checkbox']"],
    "next_button": [
        "button[aria-label='Continue to next step']",
        "button[data-easy-apply-next-button]",
        "button:has-text('Next')",
        "footer button.artdeco-button--primary",
    ],
    "review_button": [
        "button[aria-label='Review your application']",
        "button:has-text('Review')",
    ],
    "submit_button": [
        "button[aria-label='Submit application']",
        "button:has-text('Submit application')",
        "button:has-text('Submit')",
    ],
    "dismiss_button": ["button[aria-label='Dismiss']"],
    "error_feedback": [
        "div.artdeco-inline-feedback--error",
        "text=/This field is required/i",
        "text=/Invalid input/i",
        "text=/Please enter a valid/i",
        "text=/Please make a selection/i",
    ],
    "safety_reminder_dismiss": ["button[aria-label='Dismiss']", "button[data-test-dialog-secondary-btn]"],
    "resume_upload_input": ["input[type='file'][name='file']", "input[type='file']"],
    "resume_upload_label": [
        "h3:has-text('Resume')",
        "span:has-text('Resume')",
    ],
}

NAUKRI = {
    "job_card": [
        "div.srp-jobtuple-wrapper",
        "article.jobTuple",
        "div.cust-job-tuple",
    ],
    "job_id_attrs": ["data-job-id", "data-jobid"],
    "card_title": ["a.title", "a.jobTupleHeader-title", "h2 a"],
    "card_company": ["a.comp-name", "a.subTitle", "span.comp-name"],
    "card_experience": ["span.expwdth", "li.experience span.ellipsis", "span.exp-wrap"],
    "card_salary": ["span.sal-wrap span", "span.sal", "li.salary span.ellipsis"],
    "card_location": ["span.locWdth", "span.loc-wrap span", "li.location span.ellipsis"],
    "card_snippet": ["span.job-desc", "ul.job-description li", "div.job-description"],
    "card_posted": ["span.job-post-day", "span.type br + span", "span.fleft.postedDate"],
    "card_tags": ["ul.tags-gt li", "ul.tags li"],
    # Detail page
    "detail_description": [
        "div.styles_JDC__dang-inner-html__h0K4t",
        "section.job-desc",
        "div.dang-inner-html",
    ],
    "detail_title": ["h1.styles_jd-header-title__rZwM1", "h1.jd-header-title"],
    "apply_button": [
        "button#apply-button",
        "button.styles_apply-button__uJI3A",
        "span.apply-button",
    ],
    "company_site_button": [
        "button#company-site-button",
        "span.company-site-button",
    ],
}
