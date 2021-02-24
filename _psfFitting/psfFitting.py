#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 17 10:33:19 2021

@author: omartin
"""

#%% MANAGE PYTHON LIBRAIRIES
import numpy as np
import matplotlib.pyplot as plt
from psfao21 import psfao21
from telescope import telescope
from atmosphere import atmosphere
from source import source
from scipy.optimize import least_squares
import FourierUtils
from confidenceInterval import confidence_interval

#%%
def psfFitting(image,psfModelInst,x0,weights=None,fixed=None,method='trf',\
               ftol=1e-8,xtol=1e-8,gtol=1e-8,max_nfev=1000,verbose=0):
    """Fit a PSF with a parametric model solving the least-square problem
       epsilon(x) = SUM_pixel { weights * (amp * Model(x) + bck - psf)² }
    
    Parameters
    ----------
    image : numpy.ndarray
        The experimental image to be fitted
    Model : class
        The class representing the PSF model
    x0 : tuple, list, numpy.ndarray
        Initial guess for parameters
    weights : numpy.ndarray
        Least-square weighting matrix (same size as `image`)
        Inverse of the noise's variance
        Default: uniform weighting
    fixed : numpy.ndarray
        Fix some parameters to their initial value (default: None)
    method : str or callable, optional : trf, dogbox or lm
    tol:float, optional
        Tolerance for termination. For detailed control, use solver-specific options.
    options:dict, optional
        A dictionary of solver options. All methods accept the following generic options:
        maxiterint
            Maximum number of iterations to perform. Depending on the method each iteration may use several function evaluations.
        dispbool
            Set to True to print convergence messages.
        For method-specific options, see show_options.
   constraints:list of dictionaries. Each dictionary with fields:
    type:str
        Constraint type: ‘eq’ for equality, ‘ineq’ for inequality.
    fun:callable
        The function defining the constraint.
    jac:callable, optional
        The Jacobian of fun (only for SLSQP).
    args:sequence, optional
        Extra arguments to be passed to the function and Jacobian.
        Equality constraint means that the constraint function result is to be zero whereas 
        inequality means that it is to be non-negative. Note that COBYLA only supports inequality constraints.
        
    Returns
    -------
    out.x : numpy.array
            Parameters at optimum
       .dxdy : tuple of 2 floats
           PSF shift at optimum
       .flux_bck : tuple of two floats
           Estimated image flux and background
       .psf : numpy.ndarray (dim=2)
           Image of the PSF model at optimum
       .success : bool
           Minimization success
       .status : int
           Minimization status (see scipy doc)
       .message : string
           Human readable minimization status
       .active_mask : numpy.array
           Saturated bounds
       .nfev : int
           Number of function evaluations
       .cost : float
           Value of cost function at optimum
    """
    
    if weights is None: weights = np.ones_like(image)
    elif len(image)!=len(weights): raise ValueError("Keyword `weights` must have same number of elements as `psf`")
    sqW = np.sqrt(weights)
    
    # DEFINING THE COST FUNCTIONS
    class CostClass(object):
        def __init__(self):
            self.iter = 0
        def __call__(self,y):
            if (self.iter%3)==0 and (method=='lm' or verbose !=2): print("-",end="")
            self.iter += 1
            psf = psfModelInst(mini2input(y))
            return (sqW * (psf - image)).reshape(-1)
    cost = CostClass()   
    
    # DEFINING THE BOUNDS
    if fixed is not None:
        if len(fixed)!=len(x0): raise ValueError("When defined, `fixed` must be same size as `x0`")
        FREE    = [not fixed[i] for i in range(len(fixed))]
        INDFREE = np.where(FREE)[0]
        
    def get_bounds(inst):
        b_low = inst.bounds[0]
        if fixed is not None: b_low = np.take(b_low,INDFREE)
        b_up = inst.bounds[1]
        if fixed is not None: b_up = np.take(b_up,INDFREE)
        return (b_low,b_up)
    
    def input2mini(x):
        # Transform user parameters to minimizer parameters
        if fixed is None: xfree = x
        else: xfree = np.take(x,INDFREE)
        return xfree
    
    def mini2input(y,forceZero=False):
        # Transform minimizer parameters to user parameters
        if fixed is None:
            xall = y
        else:
            if forceZero:
                xall = np.zeros_like(x0)
            else:
                xall = np.copy(x0)
            for i in range(len(y)):
                xall[INDFREE[i]] = y[i]
        return xall
    
    # PERFORMING MINIMIZATION WITH CONSTRAINS AND BOUNDS
    if method == 'trf':
        result = least_squares(cost,input2mini(x0),method='trf',bounds=get_bounds(psfModelInst),\
                               ftol=ftol, xtol=xtol, gtol=gtol,max_nfev=max_nfev,verbose=verbose)
    else:
        result = least_squares(cost,input2mini(x0),method='lm',\
                               ftol=ftol, xtol=xtol, gtol=gtol,max_nfev=max_nfev,verbose=verbose)

    # update parameters
    result.x      =  mini2input(result.x)
    result.xinit  = x0
    result.im_sky = image
    result        = evaluateFittingQuality(result,psfModelInst)
    
    # 95% confidence interval
    result.xerr   = mini2input(confidence_interval(result.fun,result.jac))
    return result


def evaluateFittingQuality(result,psfModelInst):
    
    # DERIVING THE OPTIMAL MODEL
    result.im_fit = psfModelInst(result.x)
    result.im_fit = result.im_fit
    #result.psf    = psfModelInst(list(result.x[0:-3]) + [1,0,0,0])
    
    # ESTIMATING IMAGE-BASED METRICS
    def meanErrors(sky,fit):
        mse = 1e2*np.sqrt(np.sum((sky-fit)**2))/sky.sum()
        mae = 1e2*np.sum(abs(sky-fit))/sky.sum()
        fvu = 1e2*np.sqrt(np.sum((sky-fit)**2))/sky.var()
        return mse,mae,fvu
    
    result.SR_sky   = FourierUtils.getStrehl(result.im_sky,psfModelInst.tel.pupil,psfModelInst.samp)
    result.SR_fit   = FourierUtils.getStrehl(result.im_fit,psfModelInst.tel.pupil,psfModelInst.samp)
    result.FWHMx_sky , result.FWHMy_sky = FourierUtils.getFWHM(result.im_sky,psfModelInst.psInMas,nargout=2)
    result.FWHMx_fit , result.FWHMy_fit = FourierUtils.getFWHM(result.im_fit,psfModelInst.psInMas,nargout=2)
    result.mse, result.mae , result.fvu = meanErrors(result.im_sky,result.im_fit)
    
    return result