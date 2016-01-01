#!/usr/bin/env python

#######################################################################
#
#   grabpackt.py
#
#   Grab a free Packt Publishing book every day!
#
#   Author: Herman Slatman (https://hermanslatman.nl)
#
########################################################################

import requests
import ConfigParser
import argparse
import os
import sys
import smtplib
import zipfile

from lxml import etree

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.MIMEBase import MIMEBase
from email import encoders

# relevant urls
LOGIN_URL = "https://www.packtpub.com/"
GRAB_URL = "https://www.packtpub.com/packt/offers/free-learning"
BOOKS_URL = "https://www.packtpub.com/account/my-ebooks"

# some identifiers / xpaths used
FORM_ID = "packt_user_login_form"
FORM_BUILD_ID_XPATH = "//*[@id='packt-user-login-form']//*[@name='form_build_id']"
CLAIM_BOOK_XPATH = "//*[@class='float-left free-ebook']"
BOOK_LIST_XPATH = "//*[@id='product-account-list']"

# specify UTF-8 parser; otherwise errors during parser
UTF8_PARSER = etree.HTMLParser(encoding="utf-8")

# create headers:
# user agent: Chrome 41.0.2228.0 (http://www.useragentstring.com/pages/Chrome/)
# Refererer: just set to not show up as some weirdo in their logs, I guess
HEADERS = {
    'User-Agent':
        'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/41.0.2228.0 Safari/537.36',
}

# the location for the temporary download location
DOWNLOAD_DIRECTORY = os.path.dirname(os.path.realpath(__file__)) + os.sep + 'tmp' + os.sep


# a minimal helper class for storing configuration keys and value
class Config(dict):
    pass


def configure():
    """Configures the script for execution."""
    # Argument parsing only takes care of a configuration file to be specified
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', help='specify a configuration file to be read', required=False)
    args = parser.parse_args()

    # Determine the configuration file to use
    configuration_file = args.config if args.config else 'config.ini'

    # Check if the configuration file actually exists; exit if not.
    if not os.path.isfile(configuration_file):
        print 'Please specify a configuration file or rename config.ini.dist to config.ini!'
        sys.exit(1)

    # Reading configuration information
    configuration = ConfigParser.ConfigParser()
    configuration.read(configuration_file)

    # reading configuration variables
    config = Config()
    config.username = configuration.get('packt', 'user')
    config.password = configuration.get('packt', 'pass')
    config.email_enabled = configuration.getboolean('mail', 'send_mail')

    # only parse the rest when necessary
    if config.email_enabled:
        config.smtp_user = configuration.get('smtp', 'user')
        config.smtp_pass = configuration.get('smtp', 'pass')
        config.smtp_host = configuration.get('smtp', 'host')
        config.smtp_port = configuration.getint('smtp', 'port')
        config.email_to = configuration.get('mail', 'to')
        config.email_types = configuration.get('mail', 'types')
        config.email_links_only = configuration.getboolean('mail', 'links_only')
        config.email_zip = configuration.getboolean('mail', 'zip')
        config.email_force_zip = configuration.getboolean('mail', 'force_zip')
        config.email_max_size = configuration.getint('mail', 'max_size')
        config.email_delete = configuration.getboolean('mail', 'delete')

    return config


def login(config, session):
    """Performs the login on the Pack Publishing website.

    Keyword arguments:
    config -- the configuration object
    session -- a requests.Session object
    """

    # static payload contains all static post data for login. form_id is NOT the CSRF
    static_login_payload = {
        'email': config.username, 'password': config.password, 'op': 'Login', 'form_id': FORM_ID
    }

    # get the random form build id (CSRF):
    req = session.get(LOGIN_URL)
    tree = etree.HTML(req.text, UTF8_PARSER)
    form_build_id = (tree.xpath(FORM_BUILD_ID_XPATH)[0]).values()[2]

    # put form_id in payload for logging in and authenticate...
    login_payload = static_login_payload
    login_payload['form_build_id'] = form_build_id

    # perform the login by doing the post...
    req = session.post(LOGIN_URL, data=login_payload)

    return req.status_code == 200


def relocate(session):
    """Navigates to the book grabbing url."""
    # when logged in, navigate to the free learning page...
    req = session.get(GRAB_URL)

    return req.status_code == 200, req.text


def get_owned_book_ids(session):
    """Returns a list of all owned books

    Keyword arguments:
    session -- a requests.Session object
    """
    # navigate to the owned books list
    my_books = session.get(BOOKS_URL)

    # get the element that contains the list of books and then all of its childeren
    book_list_element = etree.HTML(my_books.text, UTF8_PARSER).xpath(BOOK_LIST_XPATH)[0]
    book_elements = book_list_element.getchildren()

    # iterate all of the book elements, getting and converting the nid if it exists
    owned_book_ids = [int(book_element.get('nid')) for book_element in book_elements if book_element.get('nid')]

    return owned_book_ids


def get_book_id(contents):
    """Extracts a book id from HTML.

    Keyword arguments:
    contents -- a string containing the contents of an HTML page
    """
    # parsing the new tree
    free_learning_tree = etree.HTML(contents, UTF8_PARSER)

    # extract data: a href with ids
    claim_book_element = free_learning_tree.xpath(CLAIM_BOOK_XPATH)
    a_element = claim_book_element[0].getchildren()[0]
    # format: /freelearning-claim/{id1}/{id2}; id1 and id2 are numerical, length 5
    a_href = a_element.values()[0]

    # get the exact book_id
    claim_path = a_href[1:]
    book_id = claim_path.split('/')[1]

    return book_id, claim_path


def claim(session, claim_path):
    """Claims a book.

    Keyword arguments:
    session -- a requests.Session object
    claim_path -- the path to claim a book
    """
    # construct the url to claim the book; redirect will take place
    referer = GRAB_URL
    # format: https://www.packtpub.com/freelearning-claim/{id1}/{id2}
    claim_url = LOGIN_URL + claim_path
    session.headers.update({'referer': referer})
    req = session.get(claim_url)

    return req.status_code == 200, req.text


def prepare_links(config, book_element):
    """Prepares requested links.

    Keyword arguments:
    config -- the configuration object
    book_element -- an etree.Element describing a Packt Publishing book
    """

    # get the book id
    book_id = str(book_element.get('nid'))

    #BOOKS_DOWNLOAD_URL = "https://www.packtpub.com/ebook_download/" # + {id1}/(pdf|epub|mobi)
    #CODE_DOWNLOAD_URL = "https://www.packtpub.com/code_download/" # + {id1}
    # list of valid option links
    valid_option_links = {
        'p': ('pdf', '/ebook_download/' + book_id + '/pdf'),
        'e': ('epub', '/ebook_download/' + book_id + '/epub'),
        'm': ('mobi', '/ebook_download/' + book_id + '/mobi'),
        'c': ('code', '/code_download/' + str(int(book_id) + 1))
    }

    # get the available links for the book
    available_links = book_element.xpath('.//a/@href')

    # get the links that should be executed
    links = {}
    for option in list(str(config.email_types)):
        if option in list("pemc"):
            # perform the option, e.g. get the pdf, epub, mobi and/or code link
            dl_type, link = valid_option_links[option]

            # check if the link can actually be found on the page (it exists)
            if link in available_links:
                # each of the links has to be prefixed with the login_url
                links[dl_type] = LOGIN_URL + link[1:]

    return links


def download(session, book_id, links):
    """Downloads the requested file types for a given book id.

    Keyword arguments:
    session -- a requests.Session object
    book_id -- the identifier of the book
    links -- a dictionary of dl_type => URL type
    """
    if not os.path.exists(DOWNLOAD_DIRECTORY):
        os.makedirs(DOWNLOAD_DIRECTORY)
    files = {}
    for dl_type, link in links.items():
        filename = DOWNLOAD_DIRECTORY + book_id + '.' + dl_type

        # don't download files more than once if not necessary...
        if not os.path.exists(filename):
            req = session.get(link, stream=True)
            with open(filename, 'wb') as handler:
                for chunk in req.iter_content(chunk_size=1024):
                    if chunk: # filter out keep-alive new chunks
                        handler.write(chunk)
                        #f.flush()

        files[dl_type] = filename

    return files

def create_zip(files, book_name):
    """Zips up files.

    Keyword arguments:
    files -- a dictionary of dl_type => file name
    book_name -- the name of the book
    """
    zip_filename = DOWNLOAD_DIRECTORY + book_name + '.zip'
    zip_file = zipfile.ZipFile(zip_filename, 'w')
    for dl_type, filename in files.items():
        zip_file.write(filename, book_name + '.' + dl_type)

    zip_file.close()

    return zip_filename


def prepare_attachments(config, files, zip_filename=""):
    """Prepares attachments for sending in MIME message.

    Keyword arguments:
    config -- the configuration object
    files -- a dictionary of dl_type => file name
    zip_filename -- the name of the zip file, if it has to be created
    """
    maximum_size = config.email_max_size * 1000000 # config is MB, convert to bytes.
    attachments = {}
    # check to see if there were files downloaded before
    if len(files) > 0:
        # if there were, we have to attach them, but first more logic
        if zip_filename != "":
            # the zip was actually created, we have to attach this one
            # IF: it is not bigger than the maximum file size
            size = os.path.getsize(zip_filename)
            if size <= maximum_size:
                attachments['zip'] = zip_filename
        else:
            # if zip_filename is not set, get total size of the files
            # then, if they don't exceed max, add them all
            size = 0
            for _, filename in files.items():
                size += os.path.getsize(filename)
            if size <= maximum_size:
                attachments = files

    return attachments



def create_message(config, book_name, links, attachments):
    """Construct a MIME message.

    config -- the configuration object
    book_name -- the name of the book
    links -- a list of links to include in the mail
    attachments -- a list of files to be attached to the mail
    """
    fromaddr = config.smtp_user
    toaddr = config.email_to

    msg = MIMEMultipart()

    msg['From'] = fromaddr
    msg['To'] = toaddr
    msg['Subject'] = "GrabPackt: " + book_name

    #body = "A new book was claimed by GrabPackt, called " + book_name
    #body += "<br><br>"
    #body += "Links:"
    #body += "<br>"

    #if 'pdf' in links.keys():
    #    body += '<a href="'+links['pdf']+'">PDF</a>'
    #    body += '<br>'
    #if 'epub' in links.keys():
    #    body += '<a href="'+links['epub']+'">EPUB</a>'
    #    body += '<br>'
    #if 'mobi' in links.keys():
    #    body += '<a href="'+links['mobi']+'">MOBI</a>'
    #    body += '<br>'
    #if 'code' in links.keys():
    #    body += '<a href="'+links['code']+'">CODE</a>'
    #    body += '<br>'


    body = html_mail(book_name, links)


    msg.attach(MIMEText(body, 'html'))

    # check if we need to do attachments
    if len(attachments) > 0:
        if 'zip' in attachments.keys():

            # only attach the zip file
            with open(attachments['zip'], 'rb') as attachment:

                mail_filename = book_name + '.zip'

                # creating a part
                part = MIMEBase('application', 'octet-stream')
                part.set_payload((attachment).read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment; filename="{0}"'
                                .format(mail_filename))

                msg.attach(part)

        else:
            # no zip to process; go through the keys of attachments
            for dl_type, filename in attachments.items():

                with open(filename, 'rb') as attachment:
                    mail_filename = book_name + '.' + dl_type if dl_type != 'code' else book_name + '.zip'

                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload((attachment).read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', 'attachment; filename="{0}"'.format(mail_filename))

                    msg.attach(part)

    return msg

def send_message(config, message):
    """Sends a MIME message via SMTP.

    Keyword arguments:
    config -- the configuration object
    message -- the MIME message to send
    """
    server = smtplib.SMTP(config.smtp_host, config.smtp_port)
    server.starttls()
    server.login(config.smtp_user, config.smtp_pass)
    server.sendmail(config.smtp_user, config.email_to, message.as_string())
    server.quit()

def cleanup(config, files, zip_filename=""):
    """Removes temporary downloaded files.

    Keyword arguments
    config -- the configuration object
    files -- a dictionary of dl_type => file name
    zip_filename -- the name of the zip file
    """
    # the zip file is always deleted, if it's set
    if zip_filename != "" and os.path.exists(zip_filename):
        os.remove(zip_filename)

    # check if we have to delete the downloaded files...
    if config.email_delete:
        for _, filename in files.items():
            if os.path.exists(filename):
                os.remove(filename)

def main():
    """Performs all of the logic."""

    # parsing the configuration
    config = configure()

    with requests.Session() as session:

        # set headers to something realistic; not Python requests...
        session.headers.update(HEADERS)

        # perform the login
        is_authenticated = login(config, session)

        if is_authenticated:

            # perform the relocation to the free grab page
            page_available, page_contents = relocate(session)

            # if the page is availbale (status code equaled 200), perform the rest of the process
            if page_available:

                # extract the new book id from the page contents
                new_book_id, claim_path = get_book_id(page_contents)

                # get a list of the IDs of all the books already owned
                owned_book_ids = get_owned_book_ids(session)

                # when not previously owned, grab the book
                if int(new_book_id)+1 not in owned_book_ids:

                    # perform the claim
                    has_claimed, claim_text = claim(session, claim_path)

                    if has_claimed:

                        if config.email_enabled:

                            # following is a redundant check; first verion of uniqueness;
                            # the book_id should be the nid of the first child of the list of books on the my-ebooks page
                            book_list_element = etree.HTML(claim_text, UTF8_PARSER).xpath(BOOK_LIST_XPATH)[0]
                            first_book_element = book_list_element.getchildren()[0]

                            if first_book_element.get('nid') == str(new_book_id): # equivalent: str(book_id) in first_book_element.values()
                                # the newly claimed book id is indeed a new book (not claimed before)
                                book_element = first_book_element
                                book_id = new_book_id

                                # extract the name of the book
                                book_name = book_element.get('title')

                                # get the links that should be downloaded and/or listed in mail
                                links = prepare_links(config, book_element)

                                # if we only want the links, we're basically ready for sending an email
                                # else we need some more juggling downloading the goodies
                                files = {}
                                zip_filename = ""
                                if not config.email_links_only:
                                    # first download the files to a temporary location relative to grabpackt
                                    files = download(session, book_id, links)

                                    # next check if we need to zip the downloaded files
                                    if config.email_zip:
                                        # only pack files when there is more than 1, or has been enforced
                                        if len(files) > 1 or config.email_force_zip:
                                            zip_filename = create_zip(files, book_name)


                                # prepare attachments for sending
                                attachments = prepare_attachments(config, files, zip_filename)

                                # construct the email with all necessary items...
                                message = create_message(config, book_name, links, attachments)

                                # send the email...
                                send_message(config, message)

                                # perform cleanup
                                cleanup(config, files, zip_filename)


def html_mail(book_name, links):
    mail = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
    <html xmlns="http://www.w3.org/1999/xhtml" xmlns="http://www.w3.org/1999/xhtml">
      <head>
	    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
	    <meta name="viewport" content="width=device-width" />
      </head>
      <body style="width: 100% !important; min-width: 100%; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; text-align: left; line-height: 19px; font-size: 14px; margin: 0; padding: 0;"><style type="text/css">
    a:hover {
    color: #2795b6 !important;
    }
    a:active {
    color: #2795b6 !important;
    }
    a:visited {
    color: #2ba6cb !important;
    }
    h1 a:active {
    color: #2ba6cb !important;
    }
    h2 a:active {
    color: #2ba6cb !important;
    }
    h3 a:active {
    color: #2ba6cb !important;
    }
    h4 a:active {
    color: #2ba6cb !important;
    }
    h5 a:active {
    color: #2ba6cb !important;
    }
    h6 a:active {
    color: #2ba6cb !important;
    }
    h1 a:visited {
    color: #2ba6cb !important;
    }
    h2 a:visited {
    color: #2ba6cb !important;
    }
    h3 a:visited {
    color: #2ba6cb !important;
    }
    h4 a:visited {
    color: #2ba6cb !important;
    }
    h5 a:visited {
    color: #2ba6cb !important;
    }
    h6 a:visited {
    color: #2ba6cb !important;
    }
    table.button:hover td {
    background: #2795b6 !important;
    }
    table.button:visited td {
    background: #2795b6 !important;
    }
    table.button:active td {
    background: #2795b6 !important;
    }
    table.button:hover td a {
    color: #fff !important;
    }
    table.button:visited td a {
    color: #fff !important;
    }
    table.button:active td a {
    color: #fff !important;
    }
    table.button:hover td {
    background: #2795b6 !important;
    }
    table.tiny-button:hover td {
    background: #2795b6 !important;
    }
    table.small-button:hover td {
    background: #2795b6 !important;
    }
    table.medium-button:hover td {
    background: #2795b6 !important;
    }
    table.large-button:hover td {
    background: #2795b6 !important;
    }
    table.button:hover td a {
    color: #ffffff !important;
    }
    table.button:active td a {
    color: #ffffff !important;
    }
    table.button td a:visited {
    color: #ffffff !important;
    }
    table.tiny-button:hover td a {
    color: #ffffff !important;
    }
    table.tiny-button:active td a {
    color: #ffffff !important;
    }
    table.tiny-button td a:visited {
    color: #ffffff !important;
    }
    table.small-button:hover td a {
    color: #ffffff !important;
    }
    table.small-button:active td a {
    color: #ffffff !important;
    }
    table.small-button td a:visited {
    color: #ffffff !important;
    }
    table.medium-button:hover td a {
    color: #ffffff !important;
    }
    table.medium-button:active td a {
    color: #ffffff !important;
    }
    table.medium-button td a:visited {
    color: #ffffff !important;
    }
    table.large-button:hover td a {
    color: #ffffff !important;
    }
    table.large-button:active td a {
    color: #ffffff !important;
    }
    table.large-button td a:visited {
    color: #ffffff !important;
    }
    table.secondary:hover td {
    background: #d0d0d0 !important; color: #555;
    }
    table.secondary:hover td a {
    color: #555 !important;
    }
    table.secondary td a:visited {
    color: #555 !important;
    }
    table.secondary:active td a {
    color: #555 !important;
    }
    table.success:hover td {
    background: #457a1a !important;
    }
    table.alert:hover td {
    background: #970b0e !important;
    }
    table.facebook:hover td {
    background: #2d4473 !important;
    }
    table.twitter:hover td {
    background: #0087bb !important;
    }
    table.google-plus:hover td {
    background: #CC0000 !important;
    }
    table.facebook:hover td {
    background: #2d4473 !important;
    }
    table.twitter:hover td {
    background: #0087bb !important;
    }
    table.google-plus:hover td {
    background: #CC0000 !important;
    }
    @media only screen and (max-width: 600px) {
      table[class="body"] img {
	    width: auto !important; height: auto !important;
      }
      table[class="body"] center {
	    min-width: 0 !important;
      }
      table[class="body"] .container {
	    width: 95% !important;
      }
      table[class="body"] .row {
	    width: 100% !important; display: block !important;
      }
      table[class="body"] .wrapper {
	    display: block !important; padding-right: 0 !important;
      }
      table[class="body"] .columns {
	    table-layout: fixed !important; float: none !important; width: 100% !important; padding-right: 0px !important; padding-left: 0px !important; display: block !important;
      }
      table[class="body"] .column {
	    table-layout: fixed !important; float: none !important; width: 100% !important; padding-right: 0px !important; padding-left: 0px !important; display: block !important;
      }
      table[class="body"] .wrapper.first .columns {
	    display: table !important;
      }
      table[class="body"] .wrapper.first .column {
	    display: table !important;
      }
      table[class="body"] table.columns td {
	    width: 100% !important;
      }
      table[class="body"] table.column td {
	    width: 100% !important;
      }
      table[class="body"] .columns td.one {
	    width: 8.333333% !important;
      }
      table[class="body"] .column td.one {
	    width: 8.333333% !important;
      }
      table[class="body"] .columns td.two {
	    width: 16.666666% !important;
      }
      table[class="body"] .column td.two {
	    width: 16.666666% !important;
      }
      table[class="body"] .columns td.three {
	    width: 25% !important;
      }
      table[class="body"] .column td.three {
	    width: 25% !important;
      }
      table[class="body"] .columns td.four {
	    width: 33.333333% !important;
      }
      table[class="body"] .column td.four {
	    width: 33.333333% !important;
      }
      table[class="body"] .columns td.five {
	    width: 41.666666% !important;
      }
      table[class="body"] .column td.five {
	    width: 41.666666% !important;
      }
      table[class="body"] .columns td.six {
	    width: 50% !important;
      }
      table[class="body"] .column td.six {
	    width: 50% !important;
      }
      table[class="body"] .columns td.seven {
	    width: 58.333333% !important;
      }
      table[class="body"] .column td.seven {
	    width: 58.333333% !important;
      }
      table[class="body"] .columns td.eight {
	    width: 66.666666% !important;
      }
      table[class="body"] .column td.eight {
	    width: 66.666666% !important;
      }
      table[class="body"] .columns td.nine {
	    width: 75% !important;
      }
      table[class="body"] .column td.nine {
	    width: 75% !important;
      }
      table[class="body"] .columns td.ten {
	    width: 83.333333% !important;
      }
      table[class="body"] .column td.ten {
	    width: 83.333333% !important;
      }
      table[class="body"] .columns td.eleven {
	    width: 91.666666% !important;
      }
      table[class="body"] .column td.eleven {
	    width: 91.666666% !important;
      }
      table[class="body"] .columns td.twelve {
	    width: 100% !important;
      }
      table[class="body"] .column td.twelve {
	    width: 100% !important;
      }
      table[class="body"] td.offset-by-one {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-two {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-three {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-four {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-five {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-six {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-seven {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-eight {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-nine {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-ten {
	    padding-left: 0 !important;
      }
      table[class="body"] td.offset-by-eleven {
	    padding-left: 0 !important;
      }
      table[class="body"] table.columns td.expander {
	    width: 1px !important;
      }
      table[class="body"] .right-text-pad {
	    padding-left: 10px !important;
      }
      table[class="body"] .text-pad-right {
	    padding-left: 10px !important;
      }
      table[class="body"] .left-text-pad {
	    padding-right: 10px !important;
      }
      table[class="body"] .text-pad-left {
	    padding-right: 10px !important;
      }
      table[class="body"] .hide-for-small {
	    display: none !important;
      }
      table[class="body"] .show-for-desktop {
	    display: none !important;
      }
      table[class="body"] .show-for-small {
	    display: inherit !important;
      }
      table[class="body"] .hide-for-desktop {
	    display: inherit !important;
      }
      table[class="body"] .right-text-pad {
	    padding-left: 10px !important;
      }
      table[class="body"] .left-text-pad {
	    padding-right: 10px !important;
      }
      table[class="body"] .right-text-pad {
	    padding-left: 10px !important;
      }
      table[class="body"] .left-text-pad {
	    padding-right: 10px !important;
      }
    }
    </style>
    <table class="body" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; height: 100%; width: 100%; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="center" align="center" valign="top" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: center; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;">
    <center style="width: 100%; min-width: 580px;">
    <table class="row header" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 100%; position: relative; background: #999999; padding: 0px;" bgcolor="#999999"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="center" align="center" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: center; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;" valign="top">
    <center style="width: 100%; min-width: 580px;">
    <table class="container" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: inherit; width: 580px; margin: 0 auto; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="wrapper last" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; position: relative; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 10px 0px 0px;" align="left" valign="top">
    <table class="twelve columns" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 580px; margin: 0 auto; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="six sub-columns" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; min-width: 0px; width: 50%; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0px 10px 10px 0px;" align="left" valign="top">
    <b>grabpackt.py</b> - A book a day for free.
    </td>
    <td class="six sub-columns last" style="text-align: right; vertical-align: middle; word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; min-width: 0px; width: 50%; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0px 0px 10px;" align="right" valign="middle">
    </td>
    <td class="expander" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; visibility: hidden; width: 0px; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;" align="left" valign="top"></td>
    </tr></table></td>
    </tr></table></center>
    </td>
    </tr></table><table class="container" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: inherit; width: 580px; margin: 0 auto; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;" align="left" valign="top">
    <table class="row" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 100%; position: relative; display: block; padding: 0px;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="wrapper last" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; position: relative; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 10px 0px 0px;" align="left" valign="top">
    <table class="twelve columns" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 580px; margin: 0 auto; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0px 0px 10px;" align="left" valign="top">
    <h3 style="color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; text-align: left; line-height: 1.3; word-break: normal; font-size: 32px; margin: 0; padding: 0;" align="left">"""+book_name.replace(" [eBook]", "")+"""</h3>
    </td>
    <td class="expander" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; visibility: hidden; width: 0px; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;" align="left" valign="top"></td>
    </tr></table></td>
    </tr></table><table class="row callout" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 100%; position: relative; display: block; padding: 0px;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="wrapper last" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; position: relative; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 10px 0px 20px;" align="left" valign="top">
    """


    if len(links.keys()) > 0:
        a_parts = []
        for dl_type, link in links.items():
            a_parts.append('<a href="{0}" target="_blank">{1}</a>'.format(link, dl_type.upper()))
        
        link_parts = " | ".join(a_parts)

        mail += """<table class="twelve columns" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 580px; margin: 0 auto; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="panel" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; background: #ECF8FF; margin: 0; padding: 10px; border: 1px solid #b9e5ff;" align="left" bgcolor="#ECF8FF" valign="top">
        <p style="color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; text-align: left; line-height: 19px; font-size: 14px; margin: 0 0 10px; padding: 0;" align="left"></p><center style="width: 100%; min-width: 560px;">"""+link_parts+"""</center>
        </td>
        <td class="expander" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; visibility: hidden; width: 0px; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;" align="left" valign="top"></td>
        </tr></table></td>
        </tr></table>"""
    
    
    mail += """<table class="row" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 100%; position: relative; display: block; padding: 0px;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td class="wrapper last" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; position: relative; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 10px 0px 0px;" align="left" valign="top">
    <table class="twelve columns" style="border-spacing: 0; border-collapse: collapse; vertical-align: top; text-align: left; width: 580px; margin: 0 auto; padding: 0;"><tr style="vertical-align: top; text-align: left; padding: 0;" align="left"><td align="center" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0px 0px 10px;" valign="top">
    <center style="width: 100%; min-width: 580px;">
    <p style="text-align: center; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0 0 10px; padding: 0;" align="center"><a href="https://github.com/hslatman/grabpackt.py" target="_blank" style="color: #2ba6cb; text-decoration: none;">grabpackt.py</a> by <a href="https://hermanslatman.nl" target="_blank" style="color: #2ba6cb; text-decoration: none;">Herman Slatman</a>.</p>
    </center>
    </td>
    <td class="expander" style="word-break: break-word; -webkit-hyphens: auto; -moz-hyphens: auto; hyphens: auto; border-collapse: collapse !important; vertical-align: top; text-align: left; visibility: hidden; width: 0px; color: #222222; font-family: 'Helvetica','Arial',sans-serif; font-weight: normal; line-height: 19px; font-size: 14px; margin: 0; padding: 0;" align="left" valign="top"></td>
    </tr></table></td>
    </tr></table></td>
    </tr></table></center>
    </td>
    </tr></table></body>
    </html>"""

    return mail

if __name__ == "__main__":
    main()
