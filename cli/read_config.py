#
# Copyright 2020, 2022 Hannes Holey
#
# ### MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#


from argparse import ArgumentParser

from hans.plottools import DatasetSelector


def get_parser():

    parser = ArgumentParser()
    parser.add_argument('-p', dest="path", default=".", help="path (default: data)")

    return parser


def main():

    parser = get_parser()
    args = parser.parse_args()

    files = DatasetSelector(args.path)

    for filename, data in files._ds.items():

        print(filename + ": \n" + 40 * "-")
        for name in data.ncattrs():
            print("{:20s}: {:>}".format(name, getattr(data, name)))
        print(40 * "-")


if __name__ == "__main__":
    main()
