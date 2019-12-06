#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np

class Field:

    def __init__(self, Nx, Ny, Lx, Ly):
        self.Nx = Nx
        self.Ny = Ny
        self.Lx = Ly
        self.Ly = Ly

        x = np.linspace(0.0, self.Lx, self.Nx)
        y = np.linspace(0.0, self.Ly, self.Ny)
        xx, yy = np.meshgrid(x,y)

        self.xx = xx.T
        self.yy = yy.T

        self.field_buffer = np.ones((self.ndim, self.Nx, self.Ny))

        self.field = []

        for k in range(self.ndim):
            self.field.append(np.ndarray(shape=(Nx,Ny), dtype=np.float64, buffer=self.field_buffer[k]))

class scalarField(Field):

    def __init__(self, Nx, Ny, Lx, Ly):
        self.ndim = 1
        super().__init__(Nx, Ny, Lx, Ly)

    def normal(self, loc, scale):
        self.field[0] = np.random.normal(loc, scale, size=(self.Nx, self.Ny))

    def computeGrad(self):

        grad = vectorField(self.Nx, self.Ny, self.Lx, self.Ly)
        # uniform grid
        dx = self.Lx/self.Nx

        for i in range(grad.ndim):

            pE = np.roll(self.field[0], 1,axis=i)
            pW = np.roll(self.field[0],-1,axis=i)

            # central differences
            grad.field[i] = (pE -pW)/2./dx

        return grad

    def fromFunctionXY(self, func): # get scalar field from function (height, eos)
        self.field[0]=func(self.xx, self.yy)

    def fromFunctionField(self, func, arg):
        self.field[0] = func(arg)

class vectorField(Field):

    def __init__(self, Nx, Ny, Lx, Ly):
        self.ndim = 2
        super().__init__(Nx, Ny, Lx, Ly)

class tensorField(Field):

    def __init__(self, Nx, Ny, Lx, Ly):
        self.ndim = 6
        super().__init__(Nx, Ny, Lx, Ly)

    def computeDiv(self):

        div = vectorField(self.Nx, self.Ny, self.Lx, self.Ly)

        # Indices for correct permutation in Voigt notation
        permIndices = np.array([[0,4],
                                [1,3]])
        # uniform grid
        dx = [self.Lx/self.Nx, self.Ly/self.Ny]

        for i in range(div.ndim):
            for j in range(div.ndim):
                pE = np.roll(self.field[permIndices[i,j]], 1,axis=i)
                pW = np.roll(self.field[permIndices[i,j]],-1,axis=i)

                # central differences
                div.field[i] += (pE - pW)/2./dx[i]

        return div

    def getStressNewtonian(self, p, func, velX, velY,  h):

        for i in range(int(self.ndim/2)):
            self.field[i] =  p                          # normal

        self.field[3] = func(velY, h)                   # sigma_yz
        self.field[4] = func(velX, h)                   # sigma_xz
        self.field[5] = np.zeros((self.Nx, self.Ny))    # sigma_xy
