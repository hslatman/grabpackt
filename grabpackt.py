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

from lxml import etree

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.MIMEBase import MIMEBase
from email import encoders

import pdb

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
    config.smtp_user =      configuration.get('smtp', 'user')
    config.smtp_pass =      configuration.get('smtp', 'pass')
    config.smtp_host =      configuration.get('smtp', 'host')
    config.smtp_port =      configuration.getint('smtp', 'port')
    config.email_enabled =  configuration.getint('mail', 'send_mail')
    
    # only parse the rest when necessary
    if config.email_enabled:
        config.email_to =           configuration.get('mail', 'to')
        config.email_types =        configuration.get('mail', 'types')
        config.email_links_only =   configuration.getint('mail', 'links_only')
        config.email_zip =          configuration.getint('mail', 'zip')
        config.email_force_zip =    configuration.getint('mail', 'force_zip')
        config.email_max_size =     configuration.getint('mail', 'max_size')
        config.email_delete =       configuration.getint('mail', 'delete')

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

                        # following is a redundant check; first verion of uniqueness; 
                        # TODO: might need some check for date..
                        # the book_id should be the nid of the first child of the list of books on the my-ebooks page
                        book_list_element = etree.HTML(claim_text, utf8_parser).xpath(book_list_xpath)[0]
                        first_book_element = book_list_element.getchildren()[0]

                        if first_book_element.get('nid') == str(new_book_id): # equivalent: str(book_id) in first_book_element.values()
                            # the newly claimed book id is indeed a new book (not claimed before)
                            # determine what to do next...
                            # download pdf/epub/mobi and/or code??
                            # to some tmp file, with the name of the book (values[2])
                            # or: email link/title of the new book...
                            book_name = first_book_element.get('title')

 
                            fromaddr = smtp_user
                            toaddr = email_to
 
                            msg = MIMEMultipart()
 
                            msg['From'] = fromaddr
                            msg['To'] = toaddr
                            msg['Subject'] = "GrabPackt: " + book_name
 
                            body = "A new book was claimed by GrabPackt, called " + book_name
                            msg.attach(MIMEText(body, 'plain'))
 
                            #filename = "NAME OF THE FILE WITH ITS EXTENSION"
                            #attachment = open("PATH OF THE FILE", "rb")
 
                            #part = MIMEBase('application', 'octet-stream')
                            #part.set_payload((attachment).read())
                            #encoders.encode_base64(part)
                            #part.add_header('Content-Disposition', "attachment; filename= %s" % filename)
 
                            #msg.attach(part)
 
                            server = smtplib.SMTP(smtp_host, smtp_port)
                            server.starttls()
                            server.login(fromaddr, smtp_pass)
                            text = msg.as_string()
                            server.sendmail(fromaddr, toaddr, text)
                            server.quit()

                else:
                    print "book already owned!"
        

if __name__ == "__main__":
    main()