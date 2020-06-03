#!/usr/local/bin/python3

# Copyright The Linux Foundation
#
# SPDX-License-Identifier: MIT
#
# Latest version and configuration instructions at:
#     https://github.com/brianwarner/manage-groupsio-lists-from-github-action

import os
import sys
import getopt
import requests
import json
import yaml
import re
from string import Template
from datetime import datetime
from pprint import pprint

create_directory = False
update_groupsio = False
group_configs_dir = 'groups'

user = os.environ['GROUPSIO_USERNAME'] # An account with permissions defined in README.md
password = os.environ['GROUPSIO_PASSWORD']

opts,args = getopt.getopt(sys.argv[1:],'dg')

for opt in opts:
    if opt[0] == '-d':
        create_directory = True
    elif opt[0] == '-g':
        update_groupsio = True

with open (os.path.join(group_configs_dir,'assets','config.yml'),'r') as config_file:
    config = yaml.full_load(config_file)

if not config:
    print('WARN: Could not read config: %s. Exiting.' % config_filename)
    sys.exit()

if 'group-name' in config:
    group_name = config['group-name']
else:
    print('WARN: No group name (\'group-name: ...\') defined in config. Exiting.')
    sys.exit()

if 'group-domain' in config:
    group_domain = config['group-domain']
else:
    print('WARN: Group domain (\'group-domain: ...\') not specified in config. Exiting.')

if 'main-list' in config:
    main_list = config['main-list']
else:
    print('WARN: No main list (\'main-list: ...\') defined in config. Exiting.')
    sys.exit()

if 'unified-list' in config:
    unified_list = config['unified-list']
else:
    print('INFO: No unified list (\'unified-list: ...\') defined in config, will not be created.')
    unified_list = ''

if 'index-template-file' in config:
    index_template_file = config['index-template-file']
else:
    print('WARN: No index template file (\'index-template-file: ...\') defined in config. Exiting.')
    sys.exit()

# Protect the main group.

if unified_list == main_list:
    print('ERROR: You cannot use %s as your unified list.' % main_list)
    sys.exit()

### Set up regex patterns ###

email_pattern = re.compile("[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

### Set up directory

index_template = Template(open(os.path.join(group_configs_dir,'assets',index_template_file)).read())

subgroup_index = list()

### Groups.io: Get the relevant subgroups ###

# Authenticate and get the cookie

session = requests.Session()
login = session.post(
        'https://groups.io/api/v1/login',
        data={'email':user,'password':password}).json()
cookie = session.cookies

if 'user' not in login:
    print('WARN: Could not log into Groups.io. Exiting.')
    sys.exit()

csrf = login['user']['csrf_token']

# Find all subgroups which match the list suffix, this restricts modification to
# a certain namespace of lists (e.g., can't modify membership of sensitive lists)

more_subgroups = True
next_page_token = 0
groupsio_subgroups = set()

while more_subgroups:
    subgroups_page = session.post(
            'https://groups.io/api/v1/getsubgroups?group_name=%s&limit=100&page_token=%s' %
                (group_name.replace('+','%2B'), next_page_token),
            cookies=cookie).json()

    if subgroups_page and 'data' in subgroups_page:
        for subgroup in subgroups_page['data']:
            if not subgroup['name'].endswith('+%s' % unified_list):
                groupsio_subgroups.add(subgroup['name'])
                group_domain = subgroup['org_domain']

        next_page_token = subgroups_page['next_page_token']

    if next_page_token == 0:
        more_subgroups = False

# Bail out if there aren't any matching subgroups in the group

if not groupsio_subgroups:
    sys.exit()

### Compare local subgroup membership against groups.io, resolve deltas ###

all_local_valid_members = dict()

# Open the local .yml files with subgroup definitions

all_local_subgroups_and_members = dict()
no_meta_list = list()

for root,dirs,files in os.walk(group_configs_dir):
    for f in files:
        if f.endswith('.yml') and root != os.path.join(group_configs_dir,'assets'):
            with open (yaml.full_load(os.path.join(root,f))) as config_yaml:
                all_local_subgroups_and_members[os.path.join(root,f)] = yaml.full_load(config_yaml)

if not all_local_subgroups_and_members:
    print('WARN: No lists defined. Exiting.')
    sys.exit()

# Walk through definitions

for local_file,local_subgroups_and_members in all_local_subgroups_and_members.items():
    for local_subgroup, local_groupdata in local_subgroups_and_members.items():

        # Initialize variables needed to create a directory

        header_info = list()
        contact_info = list()
        governance_info = list()
        developer_info = list()
        local_member_info = list()
        voting_member_info = list()

        # Ignore empty groups

        if not local_groupdata:
            print('INFO: Empty group definition (%s).' % local_subgroup)
            continue

        # Protect main and the unified list

        if local_subgroup in [main_list,unified_list]:
            print('INFO: You cannot modify %s. Ignoring.' % local_subgroup)
            continue

        local_valid_members = dict()

        # Walk through the members and extract the valid entries.  Note that if no
        # local member definitions are found, any non-mod/non-admin group members
        # will be removed.  This is one way to clear a subgroup.

        if 'list-members' in local_groupdata:
            for local_member in local_groupdata['list-members']:

                # Make sure email and name entries exist before proceeding

                if (not 'email' in local_member or
                        not local_member['email'] or
                        not 'name' in local_member or
                        not local_member['name']):

                    print('email or name missing, ignoring')
                    continue

                local_member_email = email_pattern.findall(local_member['email'])

                # Make sure an email was defined before proceeding

                if not local_member_email:
                    continue

                # Get the name

                local_member_name = local_member['name'].strip()

                # Store the email with the name

                local_valid_members[local_member_email[0].lower()] = local_member_name

                # Check if user doesn't want to be on meta-list

                if 'include-on-meta-list' in local_member and not local_member['include-on-meta-list']:
                    no_meta_list.append(local_member_email[0].lower())

                # Add the member to the directory

                local_member_data = ''

                # Add the member's name

                local_member_data += ('\n### **%s**\n\n' %
                    (local_member['name']))

                # Optionally add a photo

                if 'photo' in local_member and local_member['photo']:
                    local_member_data += ('<img src="%s" height=100 alt="%s">\n\n' %
                        (local_member['photo'],'Profile photo of %s' % local_member['email']))

                # Optionally add any roles

                if 'roles' in local_member and local_member['roles']:

                    for role in local_member['roles']:

                        term_begins = ''
                        term_ends = ''
                        term_info = ''

                        # Only add role data if a title is defined

                        if 'title' in role and role['title']:
                            local_member_data += '* **%s**' % role['title']

                        else:
                            continue

                        # If the role is election-based, look for a term

                        if 'term-begins' in role and role['term-begins']:

                            term_begins = role['term-begins']

                        if 'term-ends' in role and role['term-ends']:

                            term_ends = role['term-ends']

                        # Format the term sensibly based upon what's provided

                        if term_begins and term_ends:
                            term_info = '%s to %s' % (term_begins, term_ends)

                        elif term_begins:
                            term_info = 'since %s' % term_begins

                        elif term_ends:
                            term_info = 'until %s' % term_ends

                        # If the role conveys voting rights, add to voting list

                        if 'is-voting' in role and role['is-voting']:
                            local_member_data += ', voting member'

                            voting_member_info.append({
                                'name': local_member['name'],
                                'role': role['title'],
                                'term': term_info
                            })

                        if term_info:
                            local_member_data += ' (%s)\n' % term_info
                        else:
                            local_member_data += '\n'

                # Optionally add bio

                if 'bio' in local_member and local_member['bio']:
                    local_member_data += '\n%s\n' % local_member['bio']

                # Optionally add sponsoring organization

                if 'sponsor' in local_member and local_member['sponsor']:
                    if 'sponsor-website' in local_member and local_member['sponsor-website']:
                        local_member_data += ('\nParticipating on behalf of **[%s](%s)**\n' %
                            (local_member['sponsor'],local_member['sponsor-website']))

                    else:
                        local_member_data += ('\nParticipating on behalf of **%s**\n' %
                            local_member['sponsor'])

                local_member_bio_details = list()

                # Optionally add GitHub username

                if 'github-username' in local_member and local_member['github-username']:
                    local_member_bio_details.append('[GitHub](https://github.com/%s)' %
                        local_member['github-username'])

                # Optionally add Twitter username

                if 'twitter-username' in local_member and local_member['twitter-username']:
                    local_member_bio_details.append('[Twitter](https://twitter.com/%s)' %
                        local_member['twitter-username'])

                # Optionally add LinkedIn

                if 'linkedin-username' in local_member and local_member['linkedin-username']:
                    local_member_bio_details.append('[LinkedIn](https://linkedin/in/%s)' %
                        local_member['linkedin-username'])

                # Optionally add webpage

                if 'website' in local_member and local_member['website']:
                    local_member_bio_details.append('[Website](%s)' %
                        local_member['website'])

                # Optionally add pronouns

                if 'pronouns' in local_member and local_member['pronouns']:
                    local_member_bio_details.append('Pronouns: %s' % local_member['pronouns'])

                local_member_data += '\n%s\n\n' % ' | '.join(local_member_bio_details)

                local_member_info.append(local_member_data)

        # Only proceed if there's a matching subgroup at Groups.io

        calculated_subgroup_name = '%s+%s' % (group_name, local_subgroup)

        if not calculated_subgroup_name in groupsio_subgroups:
            continue

        # Add users who aren't moderators to the comparison list. Users who are mods
        # are added to a protected list.

        more_members = True
        next_page_token = 0

        permission_to_modify = True

        groupsio_members = set()
        groupsio_mods = set()

        while more_members:

            groupsio_subgroup_members_page = session.post(
                    'https://groups.io/api/v1/getmembers?group_name=%s&limit=100&page_token=%s' %
                        (calculated_subgroup_name.replace('+','%2B'), next_page_token),
                    cookies=cookie).json()

            if groupsio_subgroup_members_page['object'] == 'error':
                print('Something went wrong: %s | %s' %
                        (calculated_subgroup_name, groupsio_subgroup_members_page['type']))
                more_members = False
                permission_to_modify = False
                continue

            if groupsio_subgroup_members_page and 'data' in groupsio_subgroup_members_page:
                for subgroup_member in groupsio_subgroup_members_page['data']:
                    if 'email' in subgroup_member:

                        if subgroup_member['mod_status'] == 'sub_modstatus_none':
                            groupsio_members.add(subgroup_member['email'].lower())
                        else:
                            groupsio_mods.add(subgroup_member['email'].lower())

                next_page_token = groupsio_subgroup_members_page['next_page_token']

            if next_page_token == 0:
                more_members = False

        # Calculate the differences between the local file and Groups.io

        local_members_to_add = set()
        groupsio_members_to_remove = set()

        if permission_to_modify and update_groupsio:

            local_members_to_add = set(local_valid_members.keys()) - groupsio_members - groupsio_mods
            groupsio_members_to_remove = groupsio_members - set(local_valid_members.keys())

            # Add missing members to groups.io

            for new_member in local_members_to_add:

                new_email = new_member

                # Add a name if one was provided

                if local_valid_members[new_member]:
                    new_email = '%s <%s>' % (local_valid_members[new_member], new_member)

                add_members = session.post(
                        'https://groups.io/api/v1/directadd?group_name=%s&subgroupnames=%s&emails=%s&csrf=%s' %
                        (group_name,calculated_subgroup_name.replace('+','%2B'),new_email.replace('+','%2B'),csrf),
                        cookies=cookie).json()

                if add_members['object'] == 'error':
                    print('Something went wrong: %s | %s' %
                            (calculated_subgroup_name, add_members['type']))
                    continue

            # Prune members which are not in the local file

            pruned_emails = '\n'.join(groupsio_members_to_remove).replace('+','%2B')

            if pruned_emails:
                remove_members = session.post(
                        'https://groups.io/api/v1/bulkremovemembers?group_name=%s&emails=%s&csrf=%s' %
                        (calculated_subgroup_name.replace('+','%2B'),pruned_emails,csrf),
                        cookies=cookie).json()

                if remove_members['object'] == 'error':
                    print('Something went wrong: %s | %s' %
                            (calculated_subgroup_name, remove_members['type']))
                    continue

            # Add local members to meta list

            all_local_valid_members.update(local_valid_members)

        if create_directory:

            # Capture data for the directory

            subgroup_index.append({
                'name': local_groupdata['name'],
                'path': '%s.md' % local_subgroup
                })

            # Add the name of the subgroup

            header_info.append('# %s\n\n' %
                (local_groupdata['name']))

            # Add the logo

            if 'logo' in local_groupdata and local_groupdata['logo']:
                header_info.append('<img align="right" src="%s" width=200 alt="%s logo">\n\n' %
                    (local_groupdata['logo'], local_groupdata['name']))

            # Add a description

            header_info.append('%s\n' % local_groupdata['description'])

            # Optionally add link to about page

            if 'about-url' in local_groupdata and local_groupdata['about-url']:
                contact_info.append('[About](%s)' %
                    local_groupdata['about-url'])

            # Add the mailing list

            contact_info.append('[Mailing list](mailto:%s@%s)' %
                (local_subgroup, group_domain))

            # Optionally add development list

            if 'development-list' in local_groupdata and local_groupdata['development-list']:
                contact_info.append('[Dev list](%s)' %
                    local_groupdata['development-list'])

            # Optionally add calendar

            if 'calendar' in local_groupdata and local_groupdata['calendar']:
                contact_info.append('[Calendar](%s)' %
                    local_groupdata['calendar'])

            # Optionally add Slack

            if 'slack' in local_groupdata and local_groupdata['slack']:
                contact_info.append('[Slack](%s)' %
                    local_groupdata['slack'])

            # Optionally add Discourse

            if 'discourse' in local_groupdata and local_groupdata['discourse']:
                contact_info.append('[Discourse](%s)' %
                    local_groupdata['discourse'])

            # Optionally add IRC

            if 'irc' in local_groupdata and local_groupdata['irc']:
                contact_info.append('[IRC](%s)' %
                    local_groupdata['irc'])

            # Optionally add chat

            if 'chat' in local_groupdata and local_groupdata['chat']:
                contact_info.append('[Chat](%s)' %
                    local_groupdata['chat'])

            # Optionally add Twitter

            if 'twitter-username' in local_groupdata and local_groupdata['twitter-username']:
                contact_info.append('[Twitter](https://twitter.com/%s)' %
                    local_groupdata['twitter-username'])

            # Optionally add LinkedIn

            if 'linkedin-username' in local_groupdata and local_groupdata['linkedin-username']:
                contact_info.append('[LinkedIn](https://linkedin/company/%s)' %
                    local_groupdata['linkedin-username'])

            # Optionally add Youtube

            if 'youtube' in local_groupdata and local_groupdata['youtube']:
                contact_info.append('[YouTube](%s)' %
                    local_groupdata['youtube'])

            # Optionally add artwork

            if 'artwork' in local_groupdata and local_groupdata['artwork']:
                contact_info.append('[Artwork](%s)' %
                    local_groupdata['artwork'])

            ## Add governance data

            # Optionally add charter

            if 'charter' in local_groupdata and local_groupdata['charter']:
                governance_info.append('[Charter](%s)' %
                    local_groupdata['charter'])

            # Optionally add Code of Conduct

            if 'code-of-conduct' in local_groupdata:
                governance_info.append('[Code of Conduct](%s)' %
                    local_groupdata['code-of-conduct'])

            # Optionally add CONTRIBUTING.md

            if 'contributing' in local_groupdata:
                governance_info.append('[CONTRIBUTING.md](%s)' %
                    local_groupdata['contributing'])

            ## Add repo data

            # Optionally add repos

            if 'git' in local_groupdata:
                for repo in local_groupdata['git']:

                    developer_info.append('* [%s](%s)\n' % (
                        repo['repo'], repo['repo']))

            ## Construct the directory

            subgroup_page = '<!-- AUTOGENERATED PAGE, DO NOT EDIT IT DIRECTLY -->\n'

            if header_info:
                subgroup_page += '%s\n' % ''.join(header_info)

            if contact_info:
                subgroup_page += ' | '.join(contact_info)

            if governance_info or voting_member_info:
                subgroup_page += '\n## Governance:\n\n'

                subgroup_page += '\n%s' % ' | '.join(governance_info)

                if voting_member_info:
                    subgroup_page += ('\n| Voting members | Role | Term |\n'
                            '|---|---|---|\n')

                    for member in voting_member_info:
                        subgroup_page += ('| %s | %s | %s |\n' %
                            (member['name'], member['role'], member['term']))

            if developer_info:
                subgroup_page += '\n## Repositories:\n\n'

                subgroup_page += ''.join(developer_info)

            if local_member_info:
                subgroup_page += '\n## Members:\n'

                subgroup_page += ''.join(local_member_info)

            subgroup_page += ('------\n\nThis directory is automatically generated. '
                'To make changes, please submit a pull request against [%s](/%s)' %
                (local_file, local_file))

            # Write the page

            with open('%s.md' % local_subgroup.replace('/','-'), 'w') as groupfile:
                groupfile.write(subgroup_page)

### Manage the unified list, if defined ###

permission_to_modify = True

if unified_list and update_groupsio:

    calculated_unified_name = '%s+%s' % (group_name,unified_list)

    # Add users who aren't moderators to the comparison list. Users who are mods
    # are added to a protected list.

    more_members = True
    next_page_token = 0

    groupsio_unified_members = set()
    groupsio_unified_mods = set()

    while more_members:

        groupsio_unified_subgroup_members_page = session.post(
                'https://groups.io/api/v1/getmembers?group_name=%s&limit=100&page_token=%s' %
                    (calculated_unified_name.replace('+','%2B'),next_page_token),
                cookies=cookie).json()

        if groupsio_unified_subgroup_members_page['object'] == 'error':
            print('Something went wrong: %s | %s' %
                    (calculated_unified_name, groupsio_unified_subgroup_members_page['type']))
            permission_to_modify = False
            more_members = False
            continue

        if groupsio_unified_subgroup_members_page and 'data' in groupsio_unified_subgroup_members_page:
            for subgroup_member in groupsio_unified_subgroup_members_page['data']:
                if 'email' in subgroup_member:

                    if subgroup_member['mod_status'] == 'sub_modstatus_none':
                        groupsio_unified_members.add(subgroup_member['email'].lower())
                    else:
                        groupsio_unified_mods.add(subgroup_member['email'].lower())

            next_page_token = groupsio_unified_subgroup_members_page['next_page_token']

        if next_page_token == 0:
            more_members = False

    # Calculate the differences between the local file and Groups.io

    local_members_to_add = set(all_local_valid_members.keys()) - groupsio_unified_members - groupsio_unified_mods - set(no_meta_list)
    groupsio_members_to_remove = (groupsio_unified_members - set(all_local_valid_members.keys())).union(set(no_meta_list))

    # Add missing members to groups.io

    for new_member in local_members_to_add:

        if not permission_to_modify:
            continue

        new_email = new_member

        # Add a name if one was provided

        if all_local_valid_members[new_member]:
            new_email = '%s <%s>' % (all_local_valid_members[new_member], new_member)

        add_members = session.post(
                'https://groups.io/api/v1/directadd?group_name=%s&subgroupnames=%s&emails=%s&csrf=%s' %
                (group_name,calculated_unified_name.replace('+','%2B'),new_email.replace('+','%2B'),csrf),
                cookies=cookie).json()

        if add_members['object'] == 'error':
            print('Something went wrong: %s | %s' %
                    (calculated_subgroup_name, add_members['type']))
            permission_to_modify = False
            continue

    # Prune members which are not in the local file

    pruned_emails = '\n'.join(groupsio_members_to_remove).replace('+','%2B')

    remove_members = session.post(
            'https://groups.io/api/v1/bulkremovemembers?group_name=%s&emails=%s&csrf=%s' %
            (calculated_unified_name.replace('+','%2B'),pruned_emails,csrf),
            cookies=cookie).json()

    if remove_members['object'] == 'error':
        print('Something went wrong: %s | %s' %
                (calculated_unified_name, remove_members['type']))

if create_directory:

    ## Write the index file

    subgroup_list = ''

    # Construct the subgroup list

    for subgroup in sorted(subgroup_index, key = lambda name:name['name']):
        subgroup_list += '* [%s](%s)\n' % (subgroup['name'], subgroup['path'])

    with open('README.md', 'w') as indexfile:
        indexfile.write(index_template.substitute({
            'subgroups': subgroup_list,
            'group_configs_dir': group_configs_dir,
            'generated_date': datetime.now().strftime("%Y-%m-%d at %H:%M:%S %Z")
            }))

