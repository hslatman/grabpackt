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
login_url = "https://www.packtpub.com/"
grab_url = "https://www.packtpub.com/packt/offers/free-learning"
books_url = "https://www.packtpub.com/account/my-ebooks"
books_download_url = "https://www.packtpub.com/ebook_download/" # + {id1}/(pdf|epub|mobi)
code_download_url = "https://www.packtpub.com/code_download/" # + {id1}

# some identifiers / xpaths used
form_id = "packt_user_login_form"
form_build_id = ""
form_build_id_xpath = "//*[@id='packt-user-login-form']//*[@name='form_build_id']"
claim_book_xpath = "//*[@class='float-left free-ebook']"
book_list_xpath = "//*[@id='product-account-list']"

# specify UTF-8 parser; otherwise errors during parser
utf8_parser = etree.HTMLParser(encoding="utf-8")

# create headers:
# user agent: Chrome 41.0.2228.0 (http://www.useragentstring.com/pages/Chrome/)
# Refererer: just set to not show up as some weirdo in their logs, I guess
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36',
}

# the location for the temporary download location
download_directory = os.path.dirname(os.path.realpath(__file__)) + os.sep + 'tmp' + os.sep


# a minimal helper class for storing configuration keys and value
class Config(dict):
    pass


def configure():
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
    config.username =       configuration.get('packt', 'user')
    config.password =       configuration.get('packt', 'pass')
    config.email_enabled =  configuration.getboolean('mail', 'send_mail')
    
    # only parse the rest when necessary
    if config.email_enabled:
        config.smtp_user =          configuration.get('smtp', 'user')
        config.smtp_pass =          configuration.get('smtp', 'pass')
        config.smtp_host =          configuration.get('smtp', 'host')
        config.smtp_port =          configuration.getint('smtp', 'port')
        config.email_to =           configuration.get('mail', 'to')
        config.email_types =        configuration.get('mail', 'types')
        config.email_links_only =   configuration.getboolean('mail', 'links_only')
        config.email_zip =          configuration.getboolean('mail', 'zip')
        config.email_force_zip =    configuration.getboolean('mail', 'force_zip')
        config.email_max_size =     configuration.getint('mail', 'max_size')
        config.email_delete =       configuration.getboolean('mail', 'delete')

    return config


def perform_login(session, config):

    # static payload contains all static post data for login. form_id is NOT the CSRF
    static_login_payload = {
        'email': config.username, 'password': config.password, 'op': 'Login', 'form_id': form_id
    }

    # get the random form build id (CSRF):
    req = session.get(login_url)
    tree = etree.HTML(req.text, utf8_parser)
    form_build_id = (tree.xpath(form_build_id_xpath)[0]).values()[2] # take second element to get the id...

    # put form_id in payload for logging in and authenticate...
    login_payload = static_login_payload
    login_payload['form_build_id'] = form_build_id

    # perform the login by doing the post...
    req = session.post(login_url, data=login_payload)

    return req.status_code == 200


def perform_relocate(session):
    # when logged in, navigate to the free learning page...
    req = session.get(grab_url)
        
    return req.status_code == 200, req.text


def get_owned_book_ids(session):
    # navigate to the owned books list
    my_books = session.get(books_url)

    # get the element that contains the list of books and then all of its childeren
    book_list_element = etree.HTML(my_books.text, utf8_parser).xpath(book_list_xpath)[0]
    book_elements = book_list_element.getchildren()

    # iterate all of the book elements, getting and converting the nid if it exists
    owned_book_ids = [int(book_element.get('nid')) for book_element in book_elements if book_element.get('nid') ]

    return owned_book_ids


def get_book_id(contents):
    # parsing the new tree
    free_learning_tree = etree.HTML(contents, utf8_parser)

    # extract data: a href with ids
    claim_book_element = free_learning_tree.xpath(claim_book_xpath)
    a_element = claim_book_element[0].getchildren()[0]
    a_href = a_element.values()[0] # format: /freelearning-claim/{id1}/{id2}; id1 and id2 are numerical, length 5

    # get the exact book_id
    claim_path = a_href[1:]
    book_id = claim_path.split('/')[1]

    return book_id, claim_path


def perform_claim(session, claim_path):
    # construct the url to claim the book; redirect will take place
    referer = grab_url
    claim_url = login_url + claim_path # format: https://www.packtpub.com/freelearning-claim/{id1}/{id2}
    session.headers.update({'referer': referer})
    req = session.get(claim_url)

    return req.status_code == 200, req.text


def prepare_links(book_element, config):

    # get the book id
    book_id = str(book_element.get('nid'))

    # list of valid option links
    valid_option_links = {
        'p': ('pdf',  '/ebook_download/' + book_id + '/pdf'),
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
                links[dl_type] = login_url + link[1:]

    return links


def perform_download(session, book_id, links):
    if not os.path.exists(download_directory):
        os.makedirs(download_directory)
    files = {}
    for dl_type, link in links.items():
        filename = download_directory + book_id + '.' + dl_type

        # don't download files more than once if not necessary...
        if not os.path.exists(filename):
            req = session.get(link, stream=True)
            with open(filename, 'wb') as f:
                for chunk in req.iter_content(chunk_size=1024): 
                    if chunk: # filter out keep-alive new chunks
                        f.write(chunk)
                        #f.flush()
    
        files[dl_type] = filename

    return files
        
def perform_zip(file_list, book_name):
    zip_filename = download_directory + book_name + '.zip'
    zip = zipfile.ZipFile(zip_filename, 'w')
    for dl_type, filename in file_list.items():
        zip.write(filename, book_name + '.' + dl_type)
    
    zip.close()

    return zip_filename


def prepare_attachments(config, files, zip_filename):
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
            for dl_type, filename in files.items():
                size += os.path.getsize(filename)
            if size <= maximum_size:
                attachments = files

    return attachments



def create_message(config, book_name, links, attachments):
    fromaddr = config.smtp_user
    toaddr = config.email_to
 
    msg = MIMEMultipart()
 
    msg['From'] = fromaddr
    msg['To'] = toaddr
    msg['Subject'] = "GrabPackt: " + book_name
 

    body = "A new book was claimed by GrabPackt, called " + book_name
    body += "<br><br>"
    body += "Links:"
    body += "<br>"
    
    if 'pdf' in links.keys():
        body += '<a href="'+links['pdf']+'">PDF</a>' 
        body += '<br>'
    if 'epub' in links.keys():
        body += '<a href="'+links['epub']+'">EPUB</a>' 
        body += '<br>'
    if 'mobi' in links.keys():
        body += '<a href="'+links['mobi']+'">MOBI</a>' 
        body += '<br>'
    if 'code' in links.keys():
        body += '<a href="'+links['code']+'">CODE</a>' 
        body += '<br>'

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
                part.add_header('Content-Disposition', 'attachment; filename="{0}"'.format(mail_filename))

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

def perform_send(config, message): 
    server = smtplib.SMTP(config.smtp_host, config.smtp_port)
    server.starttls()
    server.login(config.smtp_user, config.smtp_pass)
    server.sendmail(config.smtp_user, config.email_to, message.as_string())
    server.quit()

def perform_cleanup(config, files, zip_filename):
    # the zip file is always deleted, if it's set
    if zip_filename != "" and os.path.exists(zip_filename):
        os.remove(zip_filename)

    # check if we have to delete the downloaded files...
    if config.email_delete:
        for _, filename in files.items():
            if os.path.exists(filename):
                os.remove(filename)

def main():

    # parsing the configuration
    config = configure()

    with requests.Session() as session:

        # set headers to something realistic; not Python requests...
        session.headers.update(headers)

        # perform the login
        is_authenticated = perform_login(session, config)

        if is_authenticated:
            
            # perform the relocation to the free grab page
            page_available, page_contents = perform_relocate(session)
        
            # if the page is availbale (status code equaled 200), perform the rest of the process
            if page_available:

                # extract the new book id from the page contents
                new_book_id, claim_path = get_book_id(page_contents)

                # get a list of the IDs of all the books already owned
                owned_book_ids = get_owned_book_ids(session)

                # when not previously owned, grab the book
                if int(new_book_id) not in owned_book_ids:

                    # perform the claim
                    has_claimed, claim_text = perform_claim(session, claim_path)

                    if has_claimed:

                        if config.email_enabled:

                            # following is a redundant check; first verion of uniqueness; 
                            # TODO: might need some check for date..
                            # the book_id should be the nid of the first child of the list of books on the my-ebooks page
                            book_list_element = etree.HTML(claim_text, utf8_parser).xpath(book_list_xpath)[0]
                            first_book_element = book_list_element.getchildren()[0]

                            if first_book_element.get('nid') == str(new_book_id): # equivalent: str(book_id) in first_book_element.values()
                                # the newly claimed book id is indeed a new book (not claimed before)
                                book_element = first_book_element
                                book_id = new_book_id

                                # extract the name of the book
                                book_name = book_element.get('title')

                                # get the links that should be downloaded and/or listed in mail
                                links = prepare_links(book_element, config)
 
                                # if we only want the links, we're basically ready for sending an email
                                # else we need some more juggling downloading the goodies
                                files = {}
                                zip_filename = ""
                                if not config.email_links_only:
                                    # first download the files to a temporary location relative to grabpackt
                                    files = perform_download(session, book_id, links)

                                    # next check if we need to zip the downloaded files
                                    if config.email_zip:
                                        # only pack files when there is more than 1, or has been enforced
                                        if len(files) > 1 or config.email_force_zip:
                                            zip_filename = perform_zip(files, book_name)

                                                
                                # prepare attachments for sending
                                attachments = prepare_attachments(config, files, zip_filename)

                                # construct the email with all necessary items...
                                message = create_message(config, book_name, links, attachments)

                                # send the email...
                                perform_send(config, message)

                                # perform cleanup
                                perform_cleanup(config, files, zip_filename)


                else:
                    print "book already owned!"
        

if __name__ == "__main__":
    main()