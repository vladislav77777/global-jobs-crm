"""Editable user status with separate internal delivery states."""
PUBLIC = {'New': 'Review', 'CV match': 'Review', 'Ready to apply': 'Review',
          'Applied': 'Applied', 'Conversation': 'In progress', 'Interview': 'In progress',
          'Offer': 'Offer', 'Closed': 'Closed', 'Rejection': 'Closed', 'Skipped': 'Closed',
          'Submitting': 'Needs attention', 'Delivery uncertain': 'Needs attention', 'Needs attention': 'Needs attention'}
INTERNAL = {'Review': 'New', 'Applied': 'Applied', 'In progress': 'Conversation',
            'Offer': 'Offer', 'Closed': 'Closed', 'Needs attention': 'Needs attention'}


def public_status(stage):
    return PUBLIC.get(stage, 'Needs attention')


def effective_stage(properties):
    stage, selected = properties.get('Stage'), properties.get('Status')
    # A manual user change takes precedence over stale internal workflow metadata.
    if selected in INTERNAL and selected != public_status(stage):
        return INTERNAL[selected]
    return stage
