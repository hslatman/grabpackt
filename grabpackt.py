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
config = ConfigParser.ConfigParser()
config.read(configuration_file)

# reading configuration variables
username = config.get('packt', 'user')
password = config.get('packt', 'pass')
smtp_user = config.get('smtp', 'user')
smtp_pass = config.get('smtp', 'pass')
smtp_host = config.get('smtp', 'host')
smtp_port = config.getint('smtp', 'port')
email_to = config.get('mail', 'to')
email_enabled = config.get('mail', 'send_mail')

# static payload contains all static post data for login
static_login_payload = {
    'email': username, 'password': password, 'op': 'Login', 'form_id': form_id
}


def perform_login(session):
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
    book_id = a_href[1:].split('/')[1]

    return book_id


def main():

    with requests.Session() as session:

        # set headers to something realistic; not Python requests...
        session.headers.update(headers)

        # perform the login
        is_authenticated = perform_login(session)

        if is_authenticated:
            
            # perform the relocation to the free grab page
            page_available, page_contents = perform_relocate(session)
        
            # if the page is availbale (status code equaled 200), perform the rest of the process
            if page_available:

                # extract the new book id from the page contents
                new_book_id = get_book_id(page_contents)

                # get a list of the IDs of all the books already owned
                owned_book_ids = get_owned_book_ids(session)

                # when not previously owned, grab the book
                if int(new_book_id) not in owned_book_ids:

                    # construct the url to claim the book; redirect will take place
                    referer = grab_url
                    claim_url = login_url + a_href[1:]
                    s.headers.update({'referer': referer})
                    claim = s.get(claim_url)



                    # following is a redundant check; first verion of uniqueness; 
                    # TODO: might need some check for date..
                    # the book_id should be the nid of the first child of the list of books on the my-ebooks page
                    book_list_element = etree.HTML(claim.text, utf8_parser).xpath(book_list_xpath)[0]
                    first_book_element = book_list_element.getchildren()[0]

                    if str(book_id) in first_book_element.values(): # equivalent: first_book_element.get('nid') == str(book_id)
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